import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import ePub from 'epubjs';
import { fetchBookFile } from '@/api/books';
import { fetchReadingInfo, saveReadingInfo } from '@/api/readingInfo';
import { Field, FieldLabel } from '@/components/ui/field';
import { Progress } from '@/components/ui/progress';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Spinner } from '@/components/ui/spinner';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from '@/components/ui/sheetCapture';
import { attachEdgeTapNavigation, type PageTurnAction, type PageTurnDirection } from '../readerNavigation';
import { PublicationView } from './PublicationView';
import { preserveTextDirection } from './publication';

type TocItem = {
	href: string;
	label: string;
	subitems?: TocItem[];
};

type RelocatedLocation = {
	start: { cfi: string; href?: string };
	end?: { cfi?: string; href?: string };
	atEnd?: boolean;
};

type EpubView = {
	document?: Document;
	contents?: { document?: Document };
};

type EpubRenditionEventHandler = {
	(event: 'relocated', handler: (value: RelocatedLocation) => void): void;
	(event: 'keyup', handler: (value: KeyboardEvent) => void): void;
	(event: 'rendered' | 'removed', handler: (section: unknown, view: EpubView) => void): void;
};

type EpubRenditionRuntime = {
	on: EpubRenditionEventHandler;
	off?: EpubRenditionEventHandler;
	display: (target?: string | number) => Promise<unknown>;
	destroy: () => void;
	prev: () => unknown;
	next: () => unknown;
	spread: (spread: 'always' | 'none' | 'auto', minSpreadWidth?: number) => void;
	resize: (width: number, height: number, cfi?: string) => void;
	hooks?: {
		content: {
			register: (handler: (contents: { document: Document }) => unknown) => void;
			deregister: (handler: (contents: { document: Document }) => unknown) => void;
		};
	};
};

type EpubBookRuntime = {
	renderTo: (
		element: HTMLElement,
		options: {
			width: number;
			height: number;
			spread: 'always' | 'none' | 'auto';
			minSpreadWidth: number;
			flow: 'paginated';
			view: typeof PublicationView;
		},
	) => EpubRenditionRuntime;
	ready: Promise<unknown>;
	locations: {
		generate: (chars: number) => Promise<unknown>;
		locationFromCfi: (cfi: string) => number;
		total: number;
	};
	navigation: { toc: TocItem[] };
	spine?: {
		get: (target: string) => { href: string; index: number; cfiBase?: string } | null;
		spineItems?: Array<{ href: string; index: number; cfiBase?: string }>;
	};
	destroy: () => void;
};

type EpubProgress = {
	current: number;
	total: number;
	percentage: number;
};

const EPUB_LOCATION_BREAK_SIZE = 1600;
const READING_INFO_SAVE_DELAY_MS = 750;

function animationFrame() {
	return new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
}

function getInitialSpread(): 'always' | 'none' {
	return typeof window !== 'undefined' && window.innerWidth < 768 ? 'none' : 'always';
}

function normalizedResourceHref(href: string | undefined) {
	if (!href) return '';
	const resource = href.split('#', 1)[0].split('?', 1)[0];
	try {
		return decodeURIComponent(resource).replace(/^\.\//, '');
	} catch {
		return resource.replace(/^\.\//, '');
	}
}

function normalizedTocHref(href: string | undefined) {
	if (!href) return '';
	try {
		return decodeURIComponent(href).replace(/^\.\//, '');
	} catch {
		return href.replace(/^\.\//, '');
	}
}

function flattenToc(items: TocItem[]): TocItem[] {
	return items.flatMap((item) => [item, ...flattenToc(item.subitems ?? [])]);
}

function findActiveTocHref(items: TocItem[], currentHref: string) {
	const flattened = flattenToc(items);
	const exact = flattened.find((item) => normalizedTocHref(item.href) === normalizedTocHref(currentHref));
	return exact?.href ?? flattened.find((item) => normalizedResourceHref(item.href) === normalizedResourceHref(currentHref))?.href ?? '';
}

function TocItems({ items, activeHref, onJump, depth = 0 }: {
	items: TocItem[];
	activeHref: string;
	onJump: (href: string) => void;
	depth?: number;
}) {
	return items.map((item, index) => {
		const active = Boolean(activeHref) && normalizedTocHref(item.href) === normalizedTocHref(activeHref);
		return (
			<div key={`${item.href}-${depth}-${index}`}>
				<Button
					variant='ghost'
					aria-current={active ? 'location' : undefined}
					className={`min-h-11 h-auto w-full justify-start whitespace-normal py-2 text-left ${active ? 'bg-accent font-semibold text-accent-foreground' : ''}`}
					style={{ paddingInlineStart: `${12 + Math.min(depth, 5) * 16}px` }}
					onClick={() => onJump(item.href)}
				>
					{item.label.trim() || '名称のない項目'}
				</Button>
				{item.subitems && item.subitems.length > 0 && (
					<TocItems items={item.subitems} activeHref={activeHref} onJump={onJump} depth={depth + 1} />
				)}
			</div>
		);
	});
}

function EpubReader({ bookId, pageTurnDirection }: { bookId: number; pageTurnDirection: PageTurnDirection }) {
	const mainRef = useRef<HTMLElement | null>(null);
	const viewerRef = useRef<HTMLDivElement | null>(null);
	const currentCfiRef = useRef('');
	const savedCfiRef = useRef('');
	const renditionRef = useRef<EpubRenditionRuntime | null>(null);
	const resolveTocTargetRef = useRef<(href: string) => string | number>((href) => href);
	const tocOpenRef = useRef(false);
	const saveQueueRef = useRef(Promise.resolve());
	const [spread, setSpread] = useState<'always' | 'none' | 'auto'>(getInitialSpread);
	const initialSpreadRef = useRef(spread);
	const renditionQueueRef = useRef<Promise<unknown>>(Promise.resolve());
	const [progress, setProgress] = useState<EpubProgress | null>(null);
	const [isLoading, setIsLoading] = useState(true);
	const [fileError, setFileError] = useState(false);
	const [epubData, setEpubData] = useState<ArrayBuffer | null>(null);
	const [readingInfoReady, setReadingInfoReady] = useState(false);
	const [navigation, setNavigation] = useState<{ toc: TocItem[] } | null>(null);
	const [currentHref, setCurrentHref] = useState('');
	const [history, setHistory] = useState<string[]>([]);
	const [tocOpen, setTocOpen] = useState(false);
	const storageKey = `reader-location:${bookId}`;
	const activeTocHref = navigation ? findActiveTocHref(navigation.toc, currentHref) : '';

	const enqueueRendition = useCallback((operation: (rendition: EpubRenditionRuntime) => unknown) => {
		const rendition = renditionRef.current;
		if (!rendition) return Promise.resolve();
		const queued = renditionQueueRef.current
			.catch(() => undefined)
			.then(async () => {
				if (renditionRef.current !== rendition) return;
				await operation(rendition);
				await animationFrame();
				await animationFrame();
			});
		renditionQueueRef.current = queued;
		return queued;
	}, []);

	useEffect(() => {
		tocOpenRef.current = tocOpen;
	}, [tocOpen]);

	useEffect(() => {
		let active = true;

		void fetchBookFile(bookId)
			.then((blob) => active ? blob.arrayBuffer() : null)
			.then((data) => {
				if (!active || !data) return;
				setEpubData(data);
			})
			.catch(() => {
				if (!active) return;
				setFileError(true);
				setIsLoading(false);
			});

		return () => {
			active = false;
		};
	}, [bookId]);

	useEffect(() => {
		let active = true;

		void fetchReadingInfo(bookId)
			.then((readingInfo) => {
				if (!active) return;
				// A successful server response is authoritative, including an empty
				// position for a book that has not been opened before.
				savedCfiRef.current = readingInfo.currentPosition?.startsWith('epubcfi(')
					? readingInfo.currentPosition
					: '';
			})
			.catch(() => {
				if (active) savedCfiRef.current = localStorage.getItem(storageKey) ?? '';
			})
			.finally(() => {
				if (active) setReadingInfoReady(true);
			});

		return () => {
			active = false;
		};
	}, [bookId, storageKey]);

	useEffect(() => {
		if (!viewerRef.current || !epubData || !readingInfoReady) return;
		setIsLoading(true);
		setFileError(false);
		viewerRef.current.innerHTML = '';

		const book = ePub(epubData) as unknown as EpubBookRuntime;
		resolveTocTargetRef.current = (href) => {
			const [resource, fragment] = href.split('#', 2);
			const normalizedResource = normalizedResourceHref(resource);
			const section = book.spine?.get(resource) ?? book.spine?.spineItems?.find((candidate) => {
				const candidateHref = normalizedResourceHref(candidate.href);
				return candidateHref === normalizedResource
					|| candidateHref.endsWith(`/${normalizedResource}`)
					|| normalizedResource.endsWith(`/${candidateHref}`);
			}) ?? null;
			if (!section) return href;
			if (fragment) return `${section.href}#${fragment}`;
			return section.cfiBase ? `epubcfi(${section.cfiBase}!/4/2/1:0)` : section.index;
		};
		const rendition = book.renderTo(viewerRef.current, {
			width: viewerRef.current.clientWidth,
			height: viewerRef.current.clientHeight,
			spread: initialSpreadRef.current,
			minSpreadWidth: initialSpreadRef.current === 'always' ? 1 : 800,
			flow: 'paginated',
			view: PublicationView,
		});
		renditionRef.current = rendition;
		let disposed = false;
		let readyToPersist = false;
		let restorationSettleTimer: ReturnType<typeof setTimeout> | undefined;
		let resizeFrame = 0;
		let lastSize = { width: viewerRef.current.clientWidth, height: viewerRef.current.clientHeight };
		let saveTimer: ReturnType<typeof setTimeout> | undefined;
		let pendingSave: { cfi: string; status: 'reading' | 'completed' } | null = null;
		const contentCleanups = new Map<Document, () => void>();
		const prepareContent = (contents: { document: Document }) => {
			preserveTextDirection(contents.document);
			return contents.document.fonts?.ready;
		};
		rendition.hooks?.content.register(prepareContent);

		const persistPending = () => {
			if (!pendingSave) return;
			const nextSave = pendingSave;
			pendingSave = null;
			saveQueueRef.current = saveQueueRef.current
				.catch(() => undefined)
				.then(() => saveReadingInfo(bookId, nextSave.cfi, nextSave.status))
				.then(() => undefined, () => undefined);
		};

		const scheduleSave = (cfi: string, status: 'reading' | 'completed') => {
			pendingSave = { cfi, status };
			if (saveTimer) clearTimeout(saveTimer);
			if (status === 'completed') {
				persistPending();
				return;
			}
			saveTimer = setTimeout(persistPending, READING_INFO_SAVE_DELAY_MS);
		};

		const turnPage = (action: PageTurnAction) => {
			enqueueRendition((activeRendition) => action === 'next' ? activeRendition.next() : activeRendition.prev());
		};
		const settleRestoration = () => {
			if (restorationSettleTimer) clearTimeout(restorationSettleTimer);
			restorationSettleTimer = setTimeout(() => {
				if (disposed) return;
				readyToPersist = true;
				setIsLoading(false);
			}, 250);
		};

		const handleRelocated = (location: RelocatedLocation) => {
			const shouldPersist = readyToPersist;
			const cfi = location.start.cfi;
			currentCfiRef.current = cfi;
			setCurrentHref(location.start.href ?? '');

			const locationIndex = book.locations.locationFromCfi(cfi);
			const finalLocationIndex = book.locations.total;
			if (Number.isFinite(locationIndex) && locationIndex >= 0 && Number.isFinite(finalLocationIndex) && finalLocationIndex >= 0) {
				const atEnd = location.atEnd === true;
				if (finalLocationIndex === 0 && !atEnd) {
					setProgress(null);
					localStorage.setItem(storageKey, cfi);
					if (shouldPersist) scheduleSave(cfi, 'reading');
					else settleRestoration();
					return;
				}
				const clampedIndex = atEnd ? finalLocationIndex : Math.min(locationIndex, finalLocationIndex);
				const percentage = finalLocationIndex === 0 ? 100 : Math.round((clampedIndex / finalLocationIndex) * 100);
				const readStatus = atEnd ? 'completed' : 'reading';

				setProgress({ current: clampedIndex + 1, total: finalLocationIndex + 1, percentage: Math.max(0, Math.min(100, percentage)) });
				localStorage.setItem(storageKey, cfi);
				if (shouldPersist) scheduleSave(cfi, readStatus);
				else settleRestoration();
				return;
			}

			setProgress(null);
			localStorage.setItem(storageKey, cfi);
			if (shouldPersist) scheduleSave(cfi, 'reading');
			else settleRestoration();
		};

		const handleKeyUp = (event: KeyboardEvent) => {
			if (tocOpenRef.current || event.defaultPrevented) return;
			if (event.target && typeof (event.target as Element).closest === 'function' && (event.target as Element).closest('input, textarea, select, button, a, [contenteditable="true"]')) return;
			if (event.key === 'ArrowRight') {
				turnPage(pageTurnDirection === 'rtl' ? 'previous' : 'next');
			}
			if (event.key === 'ArrowLeft') {
				turnPage(pageTurnDirection === 'rtl' ? 'next' : 'previous');
			}
		};

		const getViewDocument = (view: EpubView) => view.document ?? view.contents?.document;
		const handleRendered = (_section: unknown, view: EpubView) => {
			const contentDocument = getViewDocument(view);
			if (!contentDocument || contentCleanups.has(contentDocument)) return;

			const cleanup = attachEdgeTapNavigation(contentDocument, {
				direction: pageTurnDirection,
				isDisabled: () => tocOpenRef.current,
				getBounds: () => {
					const frame = contentDocument.defaultView?.frameElement;
					const viewerBounds = viewerRef.current?.getBoundingClientRect();
					const frameBounds = frame instanceof HTMLElement ? frame.getBoundingClientRect() : null;
					if (viewerBounds && viewerBounds.width > 0 && frameBounds) {
						return { left: viewerBounds.left - frameBounds.left, width: viewerBounds.width };
					}
					return { left: 0, width: contentDocument.defaultView?.innerWidth || contentDocument.documentElement.clientWidth };
				},
				getSelectionText: () => contentDocument.getSelection()?.toString() ?? '',
				onTurn: turnPage,
			});
			contentCleanups.set(contentDocument, cleanup);
		};
		const handleRemoved = (_section: unknown, view: EpubView) => {
			const contentDocument = getViewDocument(view);
			if (!contentDocument) return;
			contentCleanups.get(contentDocument)?.();
			contentCleanups.delete(contentDocument);
		};

		const viewer = viewerRef.current;
		const resizeObserver = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => {
			if (!readyToPersist) return;
			cancelAnimationFrame(resizeFrame);
			resizeFrame = requestAnimationFrame(() => {
				if (disposed || !viewer.clientWidth || !viewer.clientHeight) return;
				// Radix's sheet scroll lock can change the layout by one scrollbar
				// width. Re-paginating for that cosmetic shift races a TOC jump.
				// Real viewport/orientation changes are substantially larger.
				if (Math.abs(viewer.clientWidth - lastSize.width) < 24 && Math.abs(viewer.clientHeight - lastSize.height) < 24) return;
				lastSize = { width: viewer.clientWidth, height: viewer.clientHeight };
				enqueueRendition((activeRendition) => activeRendition.resize(
					viewer.clientWidth,
					viewer.clientHeight,
					currentCfiRef.current || undefined,
				));
			});
		});
		resizeObserver?.observe(viewer);

		const initialization = book.ready
			.then(async () => {
				await book.locations.generate(EPUB_LOCATION_BREAK_SIZE);
				if (disposed) return;

				setNavigation(book.navigation);
				rendition.on('relocated', handleRelocated);
				rendition.on('keyup', handleKeyUp);
				rendition.on('rendered', handleRendered);
				rendition.on('removed', handleRemoved);
				document.addEventListener('keyup', handleKeyUp);

				await rendition.display(savedCfiRef.current || undefined);
				await animationFrame();
				await animationFrame();
				if (!disposed) settleRestoration();
			})
			.catch(() => {
				if (!disposed) {
					setFileError(true);
					setIsLoading(false);
				}
			});

		return () => {
			disposed = true;
			readyToPersist = false;
			if (restorationSettleTimer) clearTimeout(restorationSettleTimer);
			cancelAnimationFrame(resizeFrame);
			resizeObserver?.disconnect();
			if (saveTimer) clearTimeout(saveTimer);
			persistPending();
			contentCleanups.forEach((cleanup) => cleanup());
			contentCleanups.clear();
			rendition.hooks?.content.deregister(prepareContent);
			rendition.off?.('relocated', handleRelocated);
			rendition.off?.('keyup', handleKeyUp);
			rendition.off?.('rendered', handleRendered);
			rendition.off?.('removed', handleRemoved);
			document.removeEventListener('keyup', handleKeyUp);
			if (renditionRef.current === rendition) renditionRef.current = null;
			resolveTocTargetRef.current = (href) => href;
			// The book owns its rendition. Wait for in-flight rendering before
			// destroying it so cleanup cannot race a queued display/resize.
			void Promise.allSettled([initialization, renditionQueueRef.current]).then(() => book.destroy());
		};
	}, [bookId, enqueueRendition, epubData, pageTurnDirection, readingInfoReady, storageKey]);

	const turnPage = (action: PageTurnAction) => {
		enqueueRendition((rendition) => action === 'next' ? rendition.next() : rendition.prev());
	};

	const changeSpread = (value: string) => {
		const nextSpread = value as 'always' | 'none' | 'auto';
		setSpread(nextSpread);
		enqueueRendition(async (rendition) => {
			const cfi = currentCfiRef.current;
			rendition.spread(nextSpread, nextSpread === 'always' ? 1 : 800);
			await rendition.display(cfi || undefined);
		});
	};

	const handleTocJump = (href: string) => {
		const previousCfi = currentCfiRef.current;
		setTocOpen(false);
		void enqueueRendition((rendition) => rendition.display(resolveTocTargetRef.current(href))).then(() => {
			if (previousCfi) setHistory((previous) => [...previous, previousCfi].slice(-20));
		});
	};

	const handleBack = () => {
		const previousCfi = history.at(-1);
		if (!previousCfi) return;
		void enqueueRendition((rendition) => rendition.display(previousCfi)).then(() => {
			setHistory((previous) => previous.slice(0, -1));
		});
	};

	if (fileError) {
		return (
			<div className='p-4'>
				<Link to='/' className='text-sm text-primary'>本棚に戻る</Link>
				<p>EPUBファイルを取得または読み込みできませんでした。</p>
			</div>
		);
	}

	return (
		<div className='flex h-[100dvh] flex-col bg-background text-foreground'>
			<header className='shrink-0 border-b p-2 sm:p-3'>
				<div className='flex flex-wrap items-center gap-2'>
					<Link to='/' className='inline-flex min-h-11 shrink-0 items-center rounded-md px-2 text-sm text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'>本棚に戻る</Link>
					<div className='ml-auto flex items-center gap-2'>
						{history.length > 0 && <Button variant='outline' aria-label='目次移動前へ戻る' onClick={handleBack} className='min-h-11 px-3'>戻る</Button>}
						<Sheet open={tocOpen} onOpenChange={setTocOpen}>
							<SheetTrigger asChild>
								<Button variant='outline' className='min-h-11 bg-white px-3 active:bg-white focus:bg-white data-[state=open]:bg-white'>目次</Button>
							</SheetTrigger>
							<SheetContent side='right' className='w-[calc(100vw-2rem)] max-w-sm [&>button]:size-11'>
								<SheetHeader className='shrink-0'>
									<SheetTitle>この書籍の目次</SheetTitle>
									<SheetDescription className='sr-only'>章や節を選んで本文へ移動します。</SheetDescription>
								</SheetHeader>
								<nav aria-label='EPUB目次' className='no-scrollbar min-h-0 flex-1 overflow-y-auto px-2 pb-4'>
					{navigation && navigation.toc.length > 0
						? <TocItems items={navigation.toc} activeHref={activeTocHref} onJump={handleTocJump} />
										: <p className='px-2 text-sm text-muted-foreground'>このEPUBには目次がありません。</p>}
								</nav>
							</SheetContent>
						</Sheet>

						<Select value={spread} onValueChange={changeSpread}>
							<SelectTrigger aria-label='ページ表示方法' className='min-h-11 w-[132px] bg-reader-background sm:w-[180px]'><SelectValue /></SelectTrigger>
							<SelectContent><SelectGroup>
								<SelectItem value='always'>常に見開き</SelectItem>
								<SelectItem value='none'>常に単ページ</SelectItem>
								<SelectItem value='auto'>自動</SelectItem>
							</SelectGroup></SelectContent>
						</Select>
					</div>
				</div>

				{progress && (
					<Field className='mt-2 w-full'>
						<FieldLabel htmlFor='reading-progress' className='text-xs sm:text-sm'>
							位置 {progress.current} / {progress.total}<span className='ml-auto'>{progress.percentage}%</span>
						</FieldLabel>
						<Progress value={progress.percentage} id='reading-progress' />
					</Field>
				)}
			</header>

			<main ref={mainRef} tabIndex={0} className='relative min-h-0 flex-1 overflow-hidden bg-white px-1 outline-none sm:px-4'>
				{isLoading && <div data-testid='epub-loading' role='status' className='absolute inset-0 z-50 flex items-center justify-center bg-reader-background'><Spinner className='size-16' /></div>}
				<div ref={viewerRef} tabIndex={1} className='h-full w-full overflow-hidden rounded bg-card shadow' />
			</main>
			<footer className='flex shrink-0 justify-center gap-3 border-t bg-background p-2'>
				<Button type='button' variant='outline' size='icon' onClick={() => turnPage('previous')} aria-label='前のページへ' className='size-11 rounded-full text-2xl'>‹</Button>
				<Button type='button' variant='outline' size='icon' onClick={() => turnPage('next')} aria-label='次のページへ' className='size-11 rounded-full text-2xl'>›</Button>
			</footer>
		</div>
	);
}

export default EpubReader;
