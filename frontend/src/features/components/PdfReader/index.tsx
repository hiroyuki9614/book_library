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

import { SelectionLayer, SelectionPluginPackage } from '@embedpdf/plugin-selection/react';
import { useEffect, useMemo, useRef, useState } from 'react';
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

const PageNavigation = ({ bookId, documentId, initialPage, pageTurnDirection }: { bookId: number; documentId: string; initialPage: number; pageTurnDirection: PageTurnDirection }) => {
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

	return (
		<div className='flex flex-col items-center gap-1'>
			<CommandButton
				pageTurnDirection={pageTurnDirection}
				prevResolved={previousCommand}
				nextResolved={nextCommand}
				state={state}
				pageInput={pageInput}
				setPageInput={setPageInputOverride}
				handleGoToPage={handleGoToPage}
			/>
			<ReadingSaveStatus status={status} retry={retry} />
		</div>
	);
};

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

function PdfTableOfContents({ documentId, open, onOpenChange }: { documentId: string; open: boolean; onOpenChange: (open: boolean) => void }) {
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

	return (
		<Sheet open={open} onOpenChange={onOpenChange}>
			<SheetTrigger asChild><Button variant='outline' className='min-h-11 px-3'>目次</Button></SheetTrigger>
			<SheetContent side='right' className='w-[calc(100vw-2rem)] max-w-sm [&>button]:size-11'>
				<SheetHeader className='shrink-0'>
					<SheetTitle>このPDFの目次</SheetTitle>
					<SheetDescription className='sr-only'>PDFに含まれるアウトラインからページへ移動します。</SheetDescription>
				</SheetHeader>
				<nav aria-label='PDF目次' className='no-scrollbar min-h-0 flex-1 overflow-y-auto px-2 pb-4'>
					{bookmarks === null
						? <p className='px-2 text-sm text-muted-foreground'>目次を読み込んでいます…</p>
						: bookmarks.length > 0
							? <BookmarkItems bookmarks={bookmarks} currentPage={state.currentPage} onJump={jump} />
							: <p className='px-2 text-sm text-muted-foreground'>このPDFには目次がありません。</p>}
				</nav>
			</SheetContent>
		</Sheet>
	);
}

const PdfViewport = ({ documentId, pageTurnDirection, navigationDisabled }: { documentId: string; pageTurnDirection: PageTurnDirection; navigationDisabled: boolean }) => {
	const containerRef = useRef<HTMLDivElement | null>(null);
	const [hasRenderedPage, setHasRenderedPage] = useState(false);
	const { provides: scroll, state } = useScroll(documentId);
	const pageStateRef = useRef(state);
	useEffect(() => {
		pageStateRef.current = state;
	}, [state]);

	useEffect(() => {
		const container = containerRef.current;
		if (!container || !scroll) return;
		return attachEdgeTapNavigation(container, {
			direction: pageTurnDirection,
			isDisabled: () => navigationDisabled,
			getBounds: () => container.getBoundingClientRect(),
			getSelectionText: () => window.getSelection()?.toString() ?? '',
			onTurn: (action) => {
				const currentState = pageStateRef.current;
				const delta = action === 'next' ? 1 : -1;
				const pageNumber = Math.max(1, Math.min(currentState.totalPages, currentState.currentPage + delta));
				if (pageNumber !== currentState.currentPage) scroll.scrollToPage({ pageNumber, behavior: 'auto' });
			},
		});
	}, [navigationDisabled, pageTurnDirection, scroll]);


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

	return (
		<div ref={containerRef} data-testid='pdf-viewport' className='relative h-full overflow-hidden'>
			{!hasRenderedPage && (
				<div
					className='pointer-events-none absolute inset-0 z-10 flex items-center justify-center bg-white/90'
					role='status'
					aria-live='polite'
				>
					<div className='flex items-center gap-2 text-sm text-muted-foreground'>
						<Spinner className='size-6' />
						<span>PDF本文を描画しています…</span>
					</div>
				</div>
			)}
			<Viewport documentId={documentId}>
				<Scroller
					documentId={documentId}
					renderPage={({ width, height, pageIndex }) => (
						<PagePointerProvider documentId={documentId} pageIndex={pageIndex}>
							<div data-pdf-page={pageIndex + 1} style={{ width, height, position: 'relative' }}>
								<RenderLayer documentId={documentId} pageIndex={pageIndex} style={{ pointerEvents: 'none' }} />
								<SelectionLayer documentId={documentId} pageIndex={pageIndex} />
							</div>
						</PagePointerProvider>
					)}
				/>
			</Viewport>
		</div>
	);
};

function ReaderWorkspace({ bookId, documentId, initialPage, pageTurnDirection }: { bookId: number; documentId: string; initialPage: number; pageTurnDirection: PageTurnDirection }) {
	const [tocOpen, setTocOpen] = useState(false);
	return (
		<div className='flex h-full flex-col'>
			<div className='mb-1 flex shrink-0 flex-wrap items-center justify-center gap-2 border-b py-1 sm:mb-2 sm:py-2'>
				<ZoomToolbar documentId={documentId} />
				<PdfTableOfContents documentId={documentId} open={tocOpen} onOpenChange={setTocOpen} />
				<PageNavigation bookId={bookId} documentId={documentId} initialPage={initialPage} pageTurnDirection={pageTurnDirection} />
			</div>
			<div className='min-h-0 flex-1 overflow-hidden'>
				<PdfViewport documentId={documentId} pageTurnDirection={pageTurnDirection} navigationDisabled={tocOpen} />
			</div>
		</div>
	);
}

function PdfReader({ bookId, pageTurnDirection = 'ltr' }: { bookId: number; pageTurnDirection?: PageTurnDirection }) {
	const [documentData, setDocumentData] = useState<ArrayBuffer | null>(null);
	const [fileError, setFileError] = useState(false);
	const [restoreError, setRestoreError] = useState(false);
	const [initialPage, setInitialPage] = useState<number | null>(null);
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
				<span className='ml-auto pr-2 text-sm font-medium text-muted-foreground'>PDFリーダー</span>
			</header>
			{restoreError && (
				<p role='alert' className='text-xs'>
					保存位置を取得できませんでした。先頭から表示しています。
				</p>
			)}
			<main tabIndex={0} className='relative min-h-0 flex-1 overflow-hidden bg-white px-2 outline-none sm:px-4'>
				<EmbedPDF engine={engine} plugins={plugins}>
					{({ activeDocumentId }) =>
						activeDocumentId && (
							<DocumentContent documentId={activeDocumentId}>
								{({ isLoaded }) =>
									isLoaded && (
										<ReaderWorkspace key={activeDocumentId} bookId={bookId} documentId={activeDocumentId} initialPage={initialPage} pageTurnDirection={pageTurnDirection} />
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
