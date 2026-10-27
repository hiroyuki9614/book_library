import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import ePub from 'epubjs';
import type Book from 'epubjs/types/book';
import type Rendition from 'epubjs/types/rendition';
import type { Location } from 'epubjs/types/rendition';
import type Contents from 'epubjs/types/contents';
import { fetchBookFile } from '@/api/books';
import { fetchReadingInfo } from '@/api/readingInfo';
import { Field, FieldLabel } from '@/components/ui/field';
import { Progress } from '@/components/ui/progress';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Spinner } from '@/components/ui/spinner';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from '@/components/ui/sheetCapture';
import ReadingSaveStatus from '../ReadingSaveStatus';
import { useReadingSave } from '@/hooks/useReadingSave';
import { attachEdgeTapNavigation, type PageTurnDirection } from '../readerNavigation';
import { PublicationView } from './PublicationView';
import { createPublicationRendition, type PublicationRendition } from './PublicationRendition';
import { flattenToc, headingToc, preserveTextDirection, type TocEntry, type PublicationSection } from './publication';

type Spread = 'always' | 'none' | 'auto';
type RuntimeRendition = PublicationRendition;
type RuntimeBook = Omit<Book, 'locations'> & {
	locations: { generate: (chars: number) => Promise<unknown>; locationFromCfi: (cfi: string) => number; total: number };
} & { package: { metadata: { direction?: string } } };
const EPUB_LOCATION_BREAK_SIZE = 1600;
const frame = () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));

function EpubReader({ bookId, pageTurnDirection }: { bookId: number; pageTurnDirection?: PageTurnDirection }) {
	const viewerRef = useRef<HTMLDivElement | null>(null);
	const currentCfiRef = useRef('');
	const renditionRef = useRef<RuntimeRendition | null>(null);
	const queueRef = useRef<Promise<unknown>>(Promise.resolve());
	const [spread, setSpread] = useState<Spread>(() => (window.innerWidth < 768 ? 'none' : 'always'));
	const initialSpread = useRef(spread);
	const [percentage, setPercentage] = useState<number | null>(null);
	const [totalPage, setTotalPage] = useState(0);
	const [currentPage, setCurrentPage] = useState(0);
	const [screenPage, setScreenPage] = useState({ page: 1, total: 1, section: 1 });
	const [bounds, setBounds] = useState({ start: true, end: false });
	const [direction, setDirection] = useState('ltr');
	const [isLoading, setIsLoading] = useState(true);
	const [fileError, setFileError] = useState(false);
	const [operationError, setOperationError] = useState(false);
	const [restoreError, setRestoreError] = useState(false);
	const [epubData, setEpubData] = useState<ArrayBuffer | null>(null);
	const [readingInfoReady, setReadingInfoReady] = useState(false);
	const [navigation, setNavigation] = useState<TocEntry[]>([]);
	const [supplement, setSupplement] = useState<TocEntry[]>([]);
	const [history, setHistory] = useState<string[]>([]);
	const [pendingOperations, setPendingOperations] = useState(0);
	const [rendererBusy, setRendererBusy] = useState(false);
	const [tocOpen, setTocOpen] = useState(false);
	const tocOpenRef = useRef(false);
	const [currentHref, setCurrentHref] = useState('');
	const setTocVisible = (open: boolean) => { tocOpenRef.current = open; setTocOpen(open); };
	const storageKey = `reader-location:${bookId}`;
	const { save, status, retry } = useReadingSave(bookId);

	const enqueue = useCallback((action: (rendition: Rendition) => unknown) => {
		const rendition = renditionRef.current;
		if (!rendition) return;
		setPendingOperations((count) => count + 1);
		queueRef.current = queueRef.current
			.catch(() => undefined)
			.then(async () => {
				if (renditionRef.current !== rendition) return;
				await action(rendition);
				await frame();
				await frame();
			})
			.catch(() => setOperationError(true))
			.finally(() => setPendingOperations((count) => Math.max(0, count - 1)));
	}, []);

	useEffect(() => {
		let active = true;
		void fetchBookFile(bookId)
			.then((blob) => blob.arrayBuffer())
			.then((data) => {
				if (active) setEpubData(data);
			})
			.catch(() => {
				if (active) {
					setFileError(true);
					setIsLoading(false);
				}
			});
		void fetchReadingInfo(bookId)
			.then((info) => {
				if (!active) return;
				// A successful server response is authoritative, including a fresh book.
				currentCfiRef.current = info.currentPosition?.startsWith('epubcfi(') ? info.currentPosition : '';
			})
			.catch(() => {
				if (active) {
					currentCfiRef.current = localStorage.getItem(storageKey) ?? '';
					setRestoreError(true);
				}
			})
			.finally(() => {
				if (active) setReadingInfoReady(true);
			});
		return () => {
			active = false;
		};
	}, [bookId, storageKey]);

	useEffect(() => {
		const viewer = viewerRef.current;
		if (!viewer || !epubData || !readingInfoReady) return;
		let disposed = false;
		let ready = false;
		let readyToPersist = false;
		let initialLocation = '';
		let resizeFrame = 0;
		let lastSize = { width: viewer.clientWidth, height: viewer.clientHeight };
		const book = ePub(epubData) as unknown as RuntimeBook;
		const rendition = createPublicationRendition(book as unknown as Book, viewer, {
			width: viewer.clientWidth,
			height: viewer.clientHeight,
			spread: initialSpread.current,
			minSpreadWidth: initialSpread.current === 'always' ? 1 : 800,
			flow: 'paginated',
			view: PublicationView,
		}, pageTurnDirection) as RuntimeRendition;
		renditionRef.current = rendition;
		const relocated = (location: Location) => {
			if (disposed || !location.start?.cfi) return;
			const cfi = location.start.cfi;
			const index = book.locations.locationFromCfi(cfi);
			const last = book.locations.total;
			const atPublicationStart = Boolean(
				location.atStart
				|| (location.start.index === 0 && location.start.displayed?.page === 1),
			);
			initialLocation = cfi;
			setPercentage(location.atEnd ? 100 : atPublicationStart ? 0 : index >= 0 && last > 0 ? Math.round((index / last) * 100) : null);
			setCurrentPage(location.atEnd ? last + 1 : atPublicationStart ? 1 : index >= 0 && last > 0 ? index + 1 : 0);
			setCurrentHref(location.start.href ?? '');
			setTotalPage(last >= 0 ? last + 1 : 0);
			if (location.start.displayed) setScreenPage({ ...location.start.displayed, section: location.start.index + 1 });
			setBounds({ start: !!location.atStart, end: !!location.atEnd });
			if (readyToPersist) {
				currentCfiRef.current = cfi;
				localStorage.setItem(storageKey, cfi);
				save(cfi, location.atEnd ? 'completed' : 'reading');
			}
		};
		const onKey = (event: KeyboardEvent) => {
			if (!ready || tocOpenRef.current || event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
			if ((event.target as Element)?.closest?.('a, input, select, textarea, button, [role="combobox"], [role="button"], [data-reader-tap-ignore], [contenteditable="true"]')) return;
			if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return;
			const eventDocument = (event.target as Node)?.ownerDocument;
			if (eventDocument?.getSelection()?.isCollapsed === false) return;
			event.preventDefault();
			const next = (event.key === 'ArrowLeft') === ((pageTurnDirection ?? book.package.metadata.direction) === 'rtl');
			enqueue((r) => (next ? r.next() : r.prev()));
		};
		const edgeDocuments = new Map<Document, () => void>();
		const content = (contents: Contents) => {
			const doc = contents.document;
			const section = book.spine.get(contents.sectionIndex);
			preserveTextDirection(doc, section?.document);
			if (!edgeDocuments.has(doc)) {
				edgeDocuments.set(doc, attachEdgeTapNavigation(doc, {
					direction: pageTurnDirection ?? (book.package.metadata.direction === 'rtl' ? 'rtl' : 'ltr'),
					isDisabled: () => !ready || tocOpenRef.current,
					getBounds: () => {
						const container = viewer.getBoundingClientRect();
						const frame = doc.defaultView?.frameElement?.getBoundingClientRect();
						return { left: container.left - (frame?.left ?? 0), width: container.width };
					},
					getSelectionText: () => doc.getSelection()?.toString() ?? '',
					onTurn: (action) => enqueue((r) => action === 'next' ? r.next() : r.prev()),
				}));
			}
			return doc.fonts?.ready;
		};
		rendition.hooks?.content.register(content);
		rendition.previewHooks(content, onKey);
		const removed = (_section: unknown, view: { document?: Document; contents?: Contents }) => {
			const doc = view.contents?.document ?? view.document;
			if (doc) { edgeDocuments.get(doc)?.(); edgeDocuments.delete(doc); }
		};
		rendition.on('removed', removed);
		rendition.on('relocated', relocated);
		rendition.on('keydown', onKey);
		const rendererError = () => { if (!disposed) setOperationError(true); };
		rendition.on('reader-error', rendererError);
		const rendererBusyChanged = (value: boolean) => { if (!disposed) setRendererBusy(value); };
		rendition.on('reader-busy', rendererBusyChanged);
		document.addEventListener('keydown', onKey);
		const observer = new ResizeObserver(() => {
			if (!ready) return;
			cancelAnimationFrame(resizeFrame);
			resizeFrame = requestAnimationFrame(() => {
				if (
					!disposed &&
					viewer.clientWidth &&
					viewer.clientHeight &&
					(viewer.clientWidth !== lastSize.width || viewer.clientHeight !== lastSize.height)
				) {
					lastSize = { width: viewer.clientWidth, height: viewer.clientHeight };
					enqueue(() => rendition.resize(viewer.clientWidth, viewer.clientHeight, currentCfiRef.current || undefined));
				}
			});
		});
		observer.observe(viewer);
		const initialization = book.ready
			.then(async () => {
				if (disposed) return;
				await book.locations.generate(EPUB_LOCATION_BREAK_SIZE);
				if (disposed) return;
				const toc = flattenToc(book.navigation.toc);
				setNavigation(toc);
				setDirection(pageTurnDirection ?? book.package.metadata.direction ?? 'ltr');
				if (toc.length <= 1 && book.spine?.each) {
					const sections: PublicationSection[] = [];
					book.spine.each((section: PublicationSection) => sections.push(section));
					try {
						const headings = await headingToc(sections, book.load.bind(book));
						if (!disposed) setSupplement(headings);
					} catch {
						if (!disposed) setOperationError(true);
					}
				}
				if (disposed) return;
				const restoreTarget = currentCfiRef.current;
				let restoredSavedTarget = false;
				try {
					await rendition.display(restoreTarget || undefined);
					restoredSavedTarget = Boolean(restoreTarget);
				} catch {
					if (!restoreTarget || disposed) throw new Error('EPUB display failed');
					setRestoreError(true);
					await rendition.display();
				}
				if (!disposed) {
					// Initial relocated events are layout observations, not user moves. Keep
					// the authoritative saved CFI until display has fully settled so a
					// transient end-of-book mapping cannot overwrite server or fallback data.
					await frame();
					await frame();
					if (disposed) return;
					const stableLocation = restoredSavedTarget ? restoreTarget : initialLocation;
					currentCfiRef.current = stableLocation;
					if (stableLocation) localStorage.setItem(storageKey, stableLocation);
					readyToPersist = true;
					ready = true;
					setIsLoading(false);
				}
			})
			.catch(() => {
				if (!disposed) {
					setFileError(true);
					setIsLoading(false);
				}
			});
		return () => {
			disposed = true;
			ready = false;
			cancelAnimationFrame(resizeFrame);
			observer.disconnect();
			for (const remove of edgeDocuments.values()) remove();
			edgeDocuments.clear();
			rendition.off('relocated', relocated);
			rendition.off('keydown', onKey);
			rendition.off('removed', removed);
			rendition.off('reader-error', rendererError);
			rendition.off('reader-busy', rendererBusyChanged);
			document.removeEventListener('keydown', onKey);
			if (renditionRef.current === rendition) renditionRef.current = null;
			// book owns rendition; destroying both races queued rendering work.
			void Promise.allSettled([initialization, queueRef.current]).then(async () => {
				await rendition.disposePreview();
				book.destroy();
			});
		};
	}, [bookId, epubData, readingInfoReady, storageKey, save, enqueue, pageTurnDirection]);

	const changeSpread = (value: string) => {
		const mode = value as Spread;
		setSpread(mode);
		enqueue(async (rendition) => {
			const cfi = currentCfiRef.current;
			await rendition.spread(mode, mode === 'always' ? 1 : 800);
			await rendition.display(cfi || undefined);
		});
	};
	const handlePrev = () => enqueue((r) => r.prev());
	const handleNext = () => enqueue((r) => r.next());
	const handleTocJump = (href: string) => {
		setTocVisible(false);
		if (currentCfiRef.current) setHistory((prev) => [...prev, currentCfiRef.current].slice(-20));
		enqueue((r) => r.display(href));
	};
	const handleBack = () => {
		const cfi = history.at(-1);
		if (!cfi) return;
		enqueue((r) => r.display(cfi));
		setHistory((prev) => prev.slice(0, -1));
	};

	if (fileError) {
		return (
			<div className='p-4'>
				<Link to='/' className='text-sm text-primary'>
					本棚に戻る
				</Link>
				<p>EPUBファイルを取得または読み込みできませんでした。</p>
			</div>
		);
	}

	const normalize = (href: string) => { try { return decodeURIComponent(href).replace(/^\.\//, ''); } catch { return href.replace(/^\.\//, ''); } };
	const resource = (href: string) => normalize(href.split('#')[0].split('?')[0]);
	const activeTocHref = navigation.find((item) => normalize(item.href) === normalize(currentHref))?.href ?? navigation.find((item) => resource(item.href) === resource(currentHref))?.href;
	return (
		<div className='flex h-[100dvh] flex-col bg-background text-foreground'>
			<header className='shrink-0 border-b p-2 sm:p-3'>
				<div className='flex flex-wrap items-center gap-2'>
					<Link
						to='/'
						className='inline-flex min-h-11 shrink-0 items-center rounded-md px-2 text-sm text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'
					>
						本棚に戻る
					</Link>
					<div className='ml-auto flex items-center gap-2'>
						{history.length > 0 && (
							<Button aria-label='目次移動前へ戻る' variant='outline' onClick={handleBack} className='min-h-11 px-3'>
								戻る
							</Button>
						)}
						<Sheet open={tocOpen} onOpenChange={setTocVisible}>
							<SheetTrigger asChild>
								<Button variant='outline' className='min-h-11 bg-white px-3 active:bg-white focus:bg-white data-[state=open]:bg-white'>
									目次
								</Button>
							</SheetTrigger>
							<SheetContent
								side='right'
								className='w-[calc(100vw-2rem)] max-w-sm data-[side=bottom]:max-h-[50vh] data-[side=top]:max-h-[50vh] [&>button]:size-11'
							>
								<SheetHeader>
									<SheetTitle>この書籍の目次</SheetTitle><SheetDescription className="sr-only">目次から本文の章へ移動します。</SheetDescription>
								</SheetHeader>
								<nav aria-label='EPUB目次' className='no-scrollbar min-h-0 overflow-y-auto px-4'>
									<p className='py-2 text-sm text-muted-foreground'>書籍の目次</p>{navigation.length === 0 && <p>このEPUBには目次がありません。</p>}
									{navigation.map((item, index) => (
										<div key={`${item.href}-${index}`} style={{ paddingInlineStart: item.depth * 16 }}>

												<Button
													variant='outline'
													className='min-h-11 h-auto w-full whitespace-normal bg-white py-2 text-left'
													aria-current={item.href === activeTocHref ? 'location' : undefined}
												onClick={() => handleTocJump(item.href)}
												>
													{item.label}
												</Button>

										</div>
									))}
									{supplement.length > 0 && (
										<>
											<p className='py-2 text-sm text-muted-foreground'>本文見出しから作成した補助目次</p>
											{supplement.map((item, index) => (
												<div key={`${item.href}-${index}`}>

														<Button
															variant='outline'
															className='min-h-11 h-auto w-full whitespace-normal bg-white py-2 text-left'
															aria-current={item.href === activeTocHref ? 'location' : undefined}
												onClick={() => handleTocJump(item.href)}
														>
															{item.label}
														</Button>

												</div>
											))}
										</>
									)}
								</nav>
							</SheetContent>
						</Sheet>

						<Select value={spread} onValueChange={changeSpread}>
							<SelectTrigger
								disabled={isLoading}
								aria-label='ページ表示方法'
								className='min-h-11 w-[132px] bg-reader-background sm:w-[180px]'
							>
								<SelectValue />
							</SelectTrigger>
							<SelectContent>
								<SelectGroup>
									<SelectItem value='always'>常に見開き</SelectItem>
									<SelectItem value='none'>常に単ページ</SelectItem>
									<SelectItem value='auto'>自動</SelectItem>
								</SelectGroup>
							</SelectContent>
						</Select>
					</div>
				</div>

				<Field className='mt-2 w-full'>
					<FieldLabel htmlFor='reading-progress' className='text-xs sm:text-sm'>
						章内ページ: {screenPage.page} / {screenPage.total}（第{screenPage.section}節）
						<br />
						本文位置: {currentPage > 0 ? `${currentPage} / ${totalPage}（約1600文字単位）` : '計算中'}
						<span className='ml-auto'>{percentage === null ? '進捗計算中' : `${percentage}%`}</span>
					</FieldLabel>
					<Progress value={percentage ?? 0} id='reading-progress' />
				</Field>
				<ReadingSaveStatus status={status} retry={retry} />
				{restoreError && (
					<p role='alert' className='text-xs'>
						保存位置を確認・復元できませんでした。現在の表示位置から再開しています。
					</p>
				)}
				{operationError && (
					<p role='alert' className='text-xs'>
						ページまたは目次を表示できませんでした。もう一度操作してください。
					</p>
				)}
			</header>

			<main tabIndex={0} className='relative min-h-0 flex-1 overflow-hidden bg-white px-1 outline-none sm:px-4'>
				{isLoading && (
					<div className='absolute inset-0 z-50 flex items-center justify-center bg-reader-background'>
						<Spinner className='size-16' />
					</div>
				)}
				<div ref={viewerRef} data-reader-viewport aria-busy={isLoading || rendererBusy || pendingOperations > 0} tabIndex={0} className='h-full w-full overflow-hidden rounded bg-card shadow' />
			</main>
			<footer
				style={{ flexDirection: direction === 'rtl' ? 'row-reverse' : 'row' }}
				className='flex shrink-0 justify-center gap-3 border-t bg-background p-2'
			>
				<Button
					type='button'
					variant='outline'
					size='icon'
					onClick={handlePrev}
					disabled={isLoading || bounds.start}
					aria-label='前のページへ'
					className='size-11 rounded-full text-2xl'
				>
					{direction === 'rtl' ? '›' : '‹'}
				</Button>
				<Button
					type='button'
					variant='outline'
					size='icon'
					onClick={handleNext}
					disabled={isLoading || bounds.end}
					aria-label='次のページへ'
					className='size-11 rounded-full text-2xl'
				>
					{direction === 'rtl' ? '‹' : '›'}
				</Button>
			</footer>
		</div>
	);
}

export default EpubReader;
