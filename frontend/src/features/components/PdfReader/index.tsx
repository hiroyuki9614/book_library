import { PdfActionType, type PdfBookmarkObject } from '@embedpdf/models';
import { BookmarkPluginPackage } from '@embedpdf/plugin-bookmark';
import { useBookmarkCapability } from '@embedpdf/plugin-bookmark/react';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from '@/components/ui/sheetCapture';
import { attachEdgeTapNavigation, type PageTurnDirection } from '../readerNavigation';
import { createPluginRegistration } from '@embedpdf/core';
import { EmbedPDF } from '@embedpdf/core/react';
import pdfiumWasmUrl from '@embedpdf/pdfium/pdfium.wasm?url';
import { usePdfiumEngine } from '@embedpdf/engines/react';
import { Link } from 'react-router-dom';
import { Spinner } from '@/components/ui/spinner';
import { ZoomToolbar } from './ZoomToolbar';
import CommandButton from './Command';
import { useCommand } from '@embedpdf/plugin-commands/react';

import { Viewport, ViewportPluginPackage } from '@embedpdf/plugin-viewport/react';
import { Scroller, ScrollPluginPackage, ScrollStrategy, useScroll, useScrollCapability } from '@embedpdf/plugin-scroll/react';
import { DocumentContent, DocumentManagerPluginPackage } from '@embedpdf/plugin-document-manager/react';
import { RenderLayer, RenderPluginPackage } from '@embedpdf/plugin-render/react';
import { ZoomPluginPackage, ZoomMode } from '@embedpdf/plugin-zoom/react';
import { InteractionManagerPluginPackage, PagePointerProvider } from '@embedpdf/plugin-interaction-manager/react';
import { CommandsPluginPackage } from '@embedpdf/plugin-commands/react';
import type { GlobalStoreState } from '@embedpdf/core';
import type { Command } from '@embedpdf/plugin-commands';
import { SCROLL_PLUGIN_ID } from '@embedpdf/plugin-scroll';
import type { ScrollState } from '@embedpdf/plugin-scroll';

import { SelectionLayer, SelectionPluginPackage, useSelectionCapability, type SelectionSelectionMenuRenderFn } from '@embedpdf/plugin-selection/react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { fetchBookFile } from '@/api/books';
import { fetchReadingInfo } from '@/api/readingInfo';
import ReadingSaveStatus from '../ReadingSaveStatus';
import { useReadingSave } from '@/hooks/useReadingSave';

export type AppState = GlobalStoreState<{
	[SCROLL_PLUGIN_ID]: ScrollState;
}>;

function createCommands(direction: PageTurnDirection): Record<string, Command<AppState>> { return {
	'nav.previous': {
		id: 'nav.previous',
		label: direction === 'rtl' ? '>' : '<',
		shortcuts: [direction === 'rtl' ? 'arrowright' : 'arrowleft', 'k'],
		action: ({ registry, documentId }) => {
			const scrollPlugin = registry.getPlugin('scroll');
			const scroll = scrollPlugin?.provides?.().forDocument(documentId);

			if (scroll) {
				scroll.scrollToPage({ pageNumber: Math.max(1, scroll.getCurrentPage() - 1), behavior: 'auto' });
			}
		},
		disabled: ({ state, documentId }) => {
			const scrollState = state.plugins.scroll.documents[documentId];
			return scrollState ? scrollState.currentPage <= 1 : true;
		},
	},
	'nav.next': {
		id: 'nav.next',
		label: direction === 'rtl' ? '<' : '>',
		shortcuts: [direction === 'rtl' ? 'arrowleft' : 'arrowright', 'j'],
		action: ({ registry, documentId }) => {
			const scrollPlugin = registry.getPlugin('scroll');
			const scroll = scrollPlugin?.provides?.().forDocument(documentId);

			if (scroll) {
				scroll.scrollToPage({ pageNumber: Math.min(scroll.getTotalPages(), scroll.getCurrentPage() + 1), behavior: 'auto' });
			}
		},
		disabled: ({ state, documentId }) => {
			const scrollState = state.plugins.scroll.documents[documentId];
			return scrollState ? scrollState.currentPage >= scrollState.totalPages : true;
		},
	},
}; }

function createPlugins(documentData: ArrayBuffer, direction: PageTurnDirection) {
	return [
		createPluginRegistration(DocumentManagerPluginPackage, {
			initialDocuments: [{ buffer: documentData, name: 'book.pdf' }],
		}),
		createPluginRegistration(ViewportPluginPackage),
		createPluginRegistration(RenderPluginPackage),
		createPluginRegistration(InteractionManagerPluginPackage),
		createPluginRegistration(SelectionPluginPackage),
		createPluginRegistration(BookmarkPluginPackage),
		createPluginRegistration(ZoomPluginPackage, {
			defaultZoomLevel: ZoomMode.FitPage,
		}),
		createPluginRegistration(ScrollPluginPackage, {
			defaultStrategy: ScrollStrategy.Vertical,
			defaultPageGap: 10,
		}),
		createPluginRegistration(CommandsPluginPackage, {
			commands: createCommands(direction),
		}),
	];
}

type PageNavigationProps = { bookId: number; documentId: string; initialPage: number; pageTurnDirection: PageTurnDirection };

const usePageNavigation = ({ bookId, documentId, initialPage, pageTurnDirection }: PageNavigationProps) => {
	const { provides: scroll, state } = useScroll(documentId);
	const [pageInputOverride, setPageInputOverride] = useState<string | null>(null);
	const pageInput = pageInputOverride ?? String(state.currentPage);
	const restoredDocument = useRef<string | null>(null);
	const { provides: scrollCapability } = useScrollCapability();
	const [targetPage, setTargetPage] = useState<number | null>(null);
	const lastPersistedPage = useRef<number | null>(null);
	const { save, status, retry } = useReadingSave(bookId);
	const previousCommand = useCommand('nav.previous', documentId);
	const nextCommand = useCommand('nav.next', documentId);

	useEffect(() => {
		if (!scrollCapability) return;
		// LayoutReady is replayed to late subscribers and carries the real page count.
		// useScroll's initial 1/1 state is not evidence that a document is ready.
		return scrollCapability.onLayoutReady((event) => {
			if (event.documentId !== documentId || restoredDocument.current === documentId) return;
			const page = Math.max(1, Math.min(initialPage, event.totalPages));
			restoredDocument.current = documentId;
			// The restored target is already authoritative. Do not PATCH it again;
			// only a subsequent user-visible page change should be persisted.
			lastPersistedPage.current = page;
			setTargetPage(page);
			scrollCapability.forDocument(documentId).scrollToPage({ pageNumber: page, behavior: 'auto' });
		});
	}, [documentId, initialPage, scrollCapability]);

	useEffect(() => {
		if (targetPage === null || restoredDocument.current !== documentId || state.totalPages < 1) return;
		if (lastPersistedPage.current === null && state.currentPage !== targetPage) return;
		if (lastPersistedPage.current === state.currentPage) return;
		lastPersistedPage.current = state.currentPage;
		save(state.currentPage, state.currentPage >= state.totalPages ? 'completed' : 'reading');
	}, [documentId, state.currentPage, state.totalPages, targetPage, save]);

	const handleGoToPage = (e: React.FormEvent<HTMLFormElement>) => {
		e.preventDefault();
		const pageNumber = parseInt(pageInput, 10);
		if (pageNumber >= 1 && pageNumber <= state.totalPages) {
			scroll?.scrollToPage({ pageNumber });
			setPageInputOverride(null);
		}
	};

	return {
		state,
		pageInput,
		setPageInput: setPageInputOverride,
		handleGoToPage,
		previousCommand,
		nextCommand,
		status,
		retry,
		pageTurnDirection,
	};
};

type PageNavigationModel = ReturnType<typeof usePageNavigation>;

const PageNavigation = ({ navigation }: { navigation: PageNavigationModel }) => (
		<div className='flex flex-col items-center gap-1'>
			<CommandButton
				pageTurnDirection={navigation.pageTurnDirection}
				prevResolved={navigation.previousCommand}
				nextResolved={navigation.nextCommand}
				state={navigation.state}
				pageInput={navigation.pageInput}
				setPageInput={navigation.setPageInput}
				handleGoToPage={navigation.handleGoToPage}
			/>
			<ReadingSaveStatus status={navigation.status} retry={navigation.retry} />
		</div>
);

function getBookmarkPage(bookmark: PdfBookmarkObject) {
	if (bookmark.target?.type === 'destination') return bookmark.target.destination.pageIndex + 1;
	if (bookmark.target?.type === 'action' && bookmark.target.action.type === PdfActionType.Goto) {
		return bookmark.target.action.destination.pageIndex + 1;
	}
	return null;
}

function BookmarkItems({ bookmarks, currentPage, onJump, depth = 0 }: {
	bookmarks: PdfBookmarkObject[];
	currentPage: number;
	onJump: (page: number) => void;
	depth?: number;
}) {
	return bookmarks.map((bookmark, index) => {
		const page = getBookmarkPage(bookmark);
		const active = page === currentPage;
		return (
			<div key={`${bookmark.title}-${page ?? 'group'}-${depth}-${index}`}>
				{page === null ? (
					<p className='min-h-11 px-3 py-2 font-medium' style={{ paddingInlineStart: `${12 + Math.min(depth, 5) * 16}px` }}>{bookmark.title || '名称のない項目'}</p>
				) : (
					<Button
						variant='ghost'
						aria-current={active ? 'location' : undefined}
						className={`min-h-11 h-auto w-full justify-start whitespace-normal py-2 text-left ${active ? 'bg-accent font-semibold text-accent-foreground' : ''}`}
						style={{ paddingInlineStart: `${12 + Math.min(depth, 5) * 16}px` }}
						onClick={() => onJump(page)}
					>
						{bookmark.title || '名称のない項目'}
					</Button>
				)}
				{bookmark.children && bookmark.children.length > 0 && (
					<BookmarkItems bookmarks={bookmark.children} currentPage={currentPage} onJump={onJump} depth={depth + 1} />
				)}
			</div>
		);
	});
}

function PdfTableOfContents({ documentId, open, onOpenChange, inline = false }: { documentId: string; open: boolean; onOpenChange: (open: boolean) => void; inline?: boolean }) {
	const { provides: bookmarksCapability } = useBookmarkCapability();
	const { provides: scroll, state } = useScroll(documentId);
	const [bookmarks, setBookmarks] = useState<PdfBookmarkObject[] | null>(null);

	useEffect(() => {
		let active = true;
		if (!bookmarksCapability) return;
		void bookmarksCapability.forDocument(documentId).getBookmarks().toPromise()
			.then((result) => {
				if (active) setBookmarks(result.bookmarks);
			})
			.catch(() => {
				if (active) setBookmarks([]);
			});
		return () => {
			active = false;
		};
	}, [bookmarksCapability, documentId]);

	const jump = (page: number) => {
		scroll?.scrollToPage({ pageNumber: Math.max(1, Math.min(page, state.totalPages)), behavior: 'auto' });
		onOpenChange(false);
	};

	const navigation = (
		<nav aria-label='PDF目次' className='no-scrollbar min-h-0 flex-1 overflow-y-auto px-2 pb-4'>
			{bookmarks === null
				? <p className='px-2 text-sm text-muted-foreground'>目次を読み込んでいます…</p>
				: bookmarks.length > 0
					? <BookmarkItems bookmarks={bookmarks} currentPage={state.currentPage} onJump={jump} />
					: <p className='px-2 text-sm text-muted-foreground'>このPDFには目次がありません。</p>}
		</nav>
	);
	if (inline) {
		return <section data-testid='pdf-mobile-toc' className='min-h-0 flex-1 overflow-hidden border-t pt-2'><h3 className='px-2 pb-1 font-medium'>このPDFの目次</h3>{navigation}</section>;
	}
	return (
		<Sheet open={open} onOpenChange={onOpenChange}>
			<SheetTrigger asChild><Button variant='outline' className='min-h-11 px-3'>目次</Button></SheetTrigger>
			<SheetContent side='right' className='w-[calc(100vw-2rem)] max-w-sm [&>button]:size-11'>
				<SheetHeader className='shrink-0'>
					<SheetTitle>このPDFの目次</SheetTitle>
					<SheetDescription className='sr-only'>PDFに含まれるアウトラインからページへ移動します。</SheetDescription>
				</SheetHeader>
				{navigation}
			</SheetContent>
		</Sheet>
	);
}

const PdfViewport = ({ documentId, pageTurnDirection, navigationDisabled }: { documentId: string; pageTurnDirection: PageTurnDirection; navigationDisabled: boolean }) => {
	const containerRef = useRef<HTMLDivElement | null>(null);
	const [hasRenderedPage, setHasRenderedPage] = useState(false);
	const [copyMode, setCopyMode] = useState(false);
	const [longPressMenuOpen, setLongPressMenuOpen] = useState(false);
	const [longPressPoint, setLongPressPoint] = useState({ x: 0, y: 0 });
	const [copyStatus, setCopyStatus] = useState<'idle' | 'copying' | 'copied' | 'unavailable' | 'failed'>('idle');
	const { provides: scroll, state } = useScroll(documentId);
	const { provides: selection } = useSelectionCapability();
	const pageStateRef = useRef(state);
	const longPressTimer = useRef<number | null>(null);
	const pointerIds = useRef(new Set<number>());
	const pointerStart = useRef<{ id: number; x: number; y: number } | null>(null);
	useEffect(() => {
		pageStateRef.current = state;
	}, [state]);

	useEffect(() => {
		const container = containerRef.current;
		if (!container || !scroll) return;
		return attachEdgeTapNavigation(container, {
			direction: pageTurnDirection,
			isDisabled: () => navigationDisabled || copyMode || longPressMenuOpen,
			getBounds: () => container.getBoundingClientRect(),
			getSelectionText: () => window.getSelection()?.toString() ?? '',
			onTurn: (action) => {
				const currentState = pageStateRef.current;
				const delta = action === 'next' ? 1 : -1;
				const pageNumber = Math.max(1, Math.min(currentState.totalPages, currentState.currentPage + delta));
				if (pageNumber !== currentState.currentPage) scroll.scrollToPage({ pageNumber, behavior: 'auto' });
			},
		});
	}, [copyMode, longPressMenuOpen, navigationDisabled, pageTurnDirection, scroll]);

	const cancelLongPress = useCallback(() => {
		if (longPressTimer.current !== null) window.clearTimeout(longPressTimer.current);
		longPressTimer.current = null;
		pointerStart.current = null;
	}, []);

	useEffect(() => {
		const container = containerRef.current;
		if (!container) return;
		const activePointerIds = pointerIds.current;
		const onPointerDown = (event: PointerEvent) => {
			const target = event.target as Element | null;
			if (target?.closest('[data-pdf-longpress-menu], [data-pdf-selection-menu], button, a, input, select, textarea, [contenteditable="true"]')) return;
			if (!event.isPrimary) {
				cancelLongPress();
				return;
			}
			if (copyMode || (event.pointerType === 'mouse' && event.button !== 0)) return;
			if (longPressMenuOpen) {
				setLongPressMenuOpen(false);
				cancelLongPress();
				return;
			}
			activePointerIds.add(event.pointerId);
			if (activePointerIds.size > 1) {
				cancelLongPress();
				return;
			}
			pointerStart.current = { id: event.pointerId, x: event.clientX, y: event.clientY };
			if (longPressTimer.current !== null) window.clearTimeout(longPressTimer.current);
			longPressTimer.current = window.setTimeout(() => {
				const start = pointerStart.current;
				if (!start || start.id !== event.pointerId || activePointerIds.size !== 1) return;
				const bounds = container.getBoundingClientRect();
				setLongPressPoint({
					x: Math.max(8, Math.min(bounds.width - 160, event.clientX - bounds.left)),
					y: Math.max(8, Math.min(bounds.height - 56, event.clientY - bounds.top)),
				});
				setCopyStatus('idle');
				setLongPressMenuOpen(true);
			}, 450);
		};
		const onPointerMove = (event: PointerEvent) => {
			const start = pointerStart.current;
			if (!start || start.id !== event.pointerId) return;
			if (Math.hypot(event.clientX - start.x, event.clientY - start.y) >= 10) cancelLongPress();
		};
		const onPointerEnd = (event: PointerEvent) => {
			activePointerIds.delete(event.pointerId);
			cancelLongPress();
		};
		const onWheel = () => cancelLongPress();
		const onTouchStart = (event: TouchEvent) => { if (event.touches.length > 1) cancelLongPress(); };
		container.addEventListener('pointerdown', onPointerDown);
		container.addEventListener('pointermove', onPointerMove);
		container.addEventListener('pointerup', onPointerEnd);
		container.addEventListener('pointercancel', onPointerEnd);
		container.addEventListener('wheel', onWheel, { passive: true });
		container.addEventListener('touchstart', onTouchStart, { passive: true });
		return () => {
			container.removeEventListener('pointerdown', onPointerDown);
			container.removeEventListener('pointermove', onPointerMove);
			container.removeEventListener('pointerup', onPointerEnd);
			container.removeEventListener('pointercancel', onPointerEnd);
			container.removeEventListener('wheel', onWheel);
			container.removeEventListener('touchstart', onTouchStart);
			activePointerIds.clear();
			cancelLongPress();
		};
	}, [cancelLongPress, copyMode, longPressMenuOpen]);

	const handleCopy = useCallback(async () => {
		const scope = selection?.forDocument(documentId);
		if (!scope) {
			setCopyStatus('unavailable');
			return;
		}
		setCopyStatus('copying');
		try {
			const parts = await scope.getSelectedText().toPromise();
			const text = parts.join('\n').trim();
			if (!text) throw new Error('empty-selection');
			if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
			else scope.copyToClipboard();
			setCopyStatus('copied');
		} catch (error) {
			setCopyStatus(error instanceof Error && error.message === 'empty-selection' ? 'unavailable' : 'failed');
		}
	}, [documentId, selection]);

	const renderSelectionMenu = useCallback<SelectionSelectionMenuRenderFn>(({ menuWrapperProps }) => (
		<div {...menuWrapperProps} data-pdf-selection-menu className='z-30 max-w-[calc(100vw-16px)] rounded-md border bg-popover p-1 shadow-lg' style={{ ...menuWrapperProps.style, width: 'max-content', maxWidth: 'calc(100vw - 16px)', pointerEvents: 'auto' }} onPointerDown={(event) => event.stopPropagation()}>
			<Button type='button' className='min-h-11 whitespace-nowrap' onClick={() => void handleCopy()}>選択範囲をコピー</Button>
		</div>
	), [handleCopy]);

	useEffect(() => () => cancelLongPress(), [cancelLongPress]);

	useEffect(() => {
		const container = containerRef.current;
		if (!container) return;
		let animationFrame = 0;

		const checkForRenderedPage = () => {
			cancelAnimationFrame(animationFrame);
			animationFrame = requestAnimationFrame(() => {
				const viewport = container.getBoundingClientRect();
				const renderedImage = Array.from(container.querySelectorAll('img')).find((image) => {
					const imageRect = image.getBoundingClientRect();
					return image.complete && image.naturalWidth > 0 && imageRect.bottom > viewport.top && imageRect.top < viewport.bottom;
				});
				setHasRenderedPage(Boolean(renderedImage));
			});
		};
		const observer = new MutationObserver(checkForRenderedPage);

		observer.observe(container, { childList: true, subtree: true, attributes: true, attributeFilter: ['src'] });
		container.addEventListener('load', checkForRenderedPage, true);
		container.addEventListener('scroll', checkForRenderedPage, true);
		window.addEventListener('resize', checkForRenderedPage);
		checkForRenderedPage();

		return () => {
			cancelAnimationFrame(animationFrame);
			observer.disconnect();
			container.removeEventListener('load', checkForRenderedPage, true);
			container.removeEventListener('scroll', checkForRenderedPage, true);
			window.removeEventListener('resize', checkForRenderedPage);
		};
	}, [documentId]);

	const copyModeMessage = copyStatus === 'copied' ? '選択範囲をコピーしました。' : copyStatus === 'unavailable' ? 'このPDFには選択できるテキストがありません。画像PDFはコピーできません。' : copyStatus === 'failed' ? 'コピーに失敗しました。ブラウザのクリップボード権限を確認してください。' : copyStatus === 'copying' ? 'コピーしています…' : '';
	return (
		<div ref={containerRef} data-testid='pdf-viewport' data-pdf-copy-mode={copyMode ? 'true' : 'false'} className='relative h-full overflow-hidden'>
			{!hasRenderedPage && (
				<div className='pointer-events-none absolute inset-0 z-10 flex items-center justify-center bg-white/90' role='status' aria-live='polite'>
					<div className='flex items-center gap-2 text-sm text-muted-foreground'><Spinner className='size-6' /><span>PDF本文を描画しています…</span></div>
				</div>
			)}
			{copyMode && (
				<div className='absolute inset-x-2 top-2 z-20 flex items-center justify-between gap-2 rounded-md border bg-background/95 px-3 py-2 text-sm shadow' data-testid='pdf-copy-mode-banner'>
					<span className='min-w-0'>コピーモード：本文を範囲選択してください。テキストレイヤーのない画像PDFはコピーできません。</span>
					<Button type='button' variant='outline' className='min-h-11 shrink-0' onClick={() => { setCopyMode(false); setCopyStatus('idle'); }}>完了</Button>
				</div>
			)}
			{copyStatus !== 'idle' && <p role='status' aria-live='polite' className='absolute inset-x-2 bottom-2 z-20 rounded-md border bg-background/95 px-3 py-2 text-sm shadow'>{copyModeMessage}</p>}
			{longPressMenuOpen && !copyMode && (
				<div data-pdf-longpress-menu className='absolute z-30 rounded-md border bg-popover p-2 shadow-lg' style={{ left: longPressPoint.x, top: longPressPoint.y }} onPointerDown={(event) => event.stopPropagation()}>
					<Button type='button' className='min-h-11 whitespace-nowrap' onClick={() => { selection?.forDocument(documentId).clear(); setCopyMode(true); setLongPressMenuOpen(false); setCopyStatus('idle'); }}>コピー</Button>
				</div>
			)}
			<Viewport documentId={documentId}>
				<Scroller
					documentId={documentId}
					renderPage={({ width, height, pageIndex }) => (
						<PagePointerProvider documentId={documentId} pageIndex={pageIndex}>
							<div data-pdf-page={pageIndex + 1} style={{ width, height, position: 'relative' }}>
								<RenderLayer documentId={documentId} pageIndex={pageIndex} style={{ pointerEvents: 'none' }} />
								<SelectionLayer documentId={documentId} pageIndex={pageIndex} selectionMenu={copyMode ? renderSelectionMenu : undefined} />
							</div>
						</PagePointerProvider>
					)}
				/>
			</Viewport>
		</div>
	);
};

function ReaderWorkspace({ bookId, documentId, initialPage, pageTurnDirection, mobileMenuOpen, onMobileMenuChange }: { bookId: number; documentId: string; initialPage: number; pageTurnDirection: PageTurnDirection; mobileMenuOpen: boolean; onMobileMenuChange: (open: boolean) => void }) {
	const [tocOpen, setTocOpen] = useState(false);
	const [mobileTocOpen, setMobileTocOpen] = useState(false);
	const navigation = usePageNavigation({ bookId, documentId, initialPage, pageTurnDirection });
	return (
		<div className='flex h-full flex-col'>
			<div className='mb-1 hidden shrink-0 flex-wrap items-center justify-center gap-2 border-b py-1 sm:mb-2 sm:py-2 md:flex'>
				<ZoomToolbar documentId={documentId} />
				<PdfTableOfContents documentId={documentId} open={tocOpen} onOpenChange={setTocOpen} />
				<PageNavigation navigation={navigation} />
			</div>
			<Sheet open={mobileMenuOpen} onOpenChange={onMobileMenuChange}>
				<SheetContent side='right' className='w-[min(88vw,22rem)] p-0 duration-[240ms] motion-reduce:duration-0 [&>button]:size-11'>
					<SheetHeader className='shrink-0 border-b pr-16'>
						<SheetTitle>操作メニュー</SheetTitle>
						<SheetDescription>拡大・ページ移動・目次</SheetDescription>
					</SheetHeader>
					<div className='no-scrollbar min-h-0 flex-1 overflow-y-auto p-4'>
						<div className='flex flex-col gap-3'>
							<ZoomToolbar documentId={documentId} compact />
							<PageNavigation navigation={navigation} />
							<Button type='button' variant='outline' className='min-h-11 w-full justify-start' aria-expanded={mobileTocOpen} onClick={() => setMobileTocOpen((open) => !open)}>目次</Button>
							{mobileTocOpen && <PdfTableOfContents inline documentId={documentId} open={mobileTocOpen} onOpenChange={setMobileTocOpen} />}
						</div>
					</div>
				</SheetContent>
			</Sheet>
			<div className='min-h-0 flex-1 overflow-hidden'>
				<PdfViewport documentId={documentId} pageTurnDirection={pageTurnDirection} navigationDisabled={tocOpen || mobileMenuOpen} />
			</div>
		</div>
	);
}

function PdfReader({ bookId, bookTitle, pageTurnDirection = 'ltr' }: { bookId: number; bookTitle?: string; pageTurnDirection?: PageTurnDirection }) {
	const [documentData, setDocumentData] = useState<ArrayBuffer | null>(null);
	const [fileError, setFileError] = useState(false);
	const [restoreError, setRestoreError] = useState(false);
	const [initialPage, setInitialPage] = useState<number | null>(null);
	const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
	const { engine, isLoading, error: engineError } = usePdfiumEngine({ wasmUrl: new URL(pdfiumWasmUrl, window.location.href).href });

	useEffect(() => {
		let active = true;

		void fetchBookFile(bookId)
			.then((blob) => blob.arrayBuffer())
			.then((data) => {
				if (!active) return;
				setDocumentData(data);
			})
			.catch(() => {
				if (active) setFileError(true);
			});

		void fetchReadingInfo(bookId)
			.then((readingInfo) => {
				if (active) setInitialPage(readingInfo.currentPage);
			})
			.catch(() => {
				if (active) {
					setInitialPage(1);
					setRestoreError(true);
				}
			});

		return () => {
			active = false;
		};
	}, [bookId]);

	const plugins = useMemo(() => (documentData ? createPlugins(documentData, pageTurnDirection) : []), [documentData, pageTurnDirection]);

	if (fileError || engineError) return <p role='alert'>PDFファイルまたは描画エンジンを読み込めませんでした。</p>;

	if (isLoading || !engine || !documentData || initialPage === null) {
		return (
			<div className='overflow-hidden rounded-lg border border-gray-300 bg-white dark:border-gray-700 dark:bg-gray-900'>
				<div className='flex h-[400px] items-center justify-center'>
					<div className='flex items-center gap-2 text-gray-500 dark:text-gray-400'>
						<span className='text-sm'>Loading PDF Engine...</span>
					</div>
				</div>
			</div>
		);
	}

	return (
		<div className='flex h-[100dvh] flex-col bg-background text-foreground'>
			<header className='flex shrink-0 items-center border-b p-2 sm:p-3'>
				<Link
					to='/'
					className='inline-flex min-h-11 items-center rounded-md px-2 text-sm text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'
				>
					本棚に戻る
				</Link>
				<span className='min-w-0 flex-1 truncate px-2 text-center text-sm font-medium text-foreground'>{bookTitle || 'PDFリーダー'}</span>
				<Button type='button' variant='outline' className='min-h-11 shrink-0 px-3 md:hidden' aria-label='操作メニューを開く' onClick={() => setMobileMenuOpen(true)}>メニュー</Button>
			</header>
			{restoreError && (
				<p role='alert' className='text-xs'>
					保存位置を取得できませんでした。先頭から表示しています。
				</p>
			)}
			<main tabIndex={0} className='relative min-h-0 flex-1 overflow-hidden bg-white px-0 outline-none sm:px-4'>
				<EmbedPDF engine={engine} plugins={plugins}>
					{({ activeDocumentId }) =>
						activeDocumentId && (
							<DocumentContent documentId={activeDocumentId}>
								{({ isLoaded }) =>
									isLoaded && (
										<ReaderWorkspace key={activeDocumentId} bookId={bookId} documentId={activeDocumentId} initialPage={initialPage} pageTurnDirection={pageTurnDirection} mobileMenuOpen={mobileMenuOpen} onMobileMenuChange={setMobileMenuOpen} />
									)
								}
							</DocumentContent>
						)
					}
				</EmbedPDF>
			</main>
		</div>
	);
}

export default PdfReader;
