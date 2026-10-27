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

const myCommands: Record<string, Command<AppState>> = {
	'nav.previous': {
		id: 'nav.previous',
		label: '<',
		shortcuts: ['arrowleft', 'k'],
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
		label: '>',
		shortcuts: ['arrowright', 'j'],
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
};

function createPlugins(documentData: ArrayBuffer) {
	return [
		createPluginRegistration(DocumentManagerPluginPackage, {
			initialDocuments: [{ buffer: documentData, name: 'book.pdf' }],
		}),
		createPluginRegistration(ViewportPluginPackage),
		createPluginRegistration(RenderPluginPackage),
		createPluginRegistration(InteractionManagerPluginPackage),
		createPluginRegistration(SelectionPluginPackage),
		createPluginRegistration(ZoomPluginPackage, {
			defaultZoomLevel: ZoomMode.FitPage,
		}),
		createPluginRegistration(ScrollPluginPackage, {
			defaultStrategy: ScrollStrategy.Vertical,
			defaultPageGap: 10,
		}),
		createPluginRegistration(CommandsPluginPackage, {
			commands: myCommands,
		}),
	];
}

const PageNavigation = ({ bookId, documentId, initialPage }: { bookId: number; documentId: string; initialPage: number }) => {
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
			lastPersistedPage.current = null;
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

const PdfViewport = ({ documentId }: { documentId: string }) => {
	const containerRef = useRef<HTMLDivElement | null>(null);
	const [hasRenderedPage, setHasRenderedPage] = useState(false);

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
		<div ref={containerRef} className='relative h-full overflow-hidden'>
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

function PdfReader({ bookId }: { bookId: number }) {
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

	const plugins = useMemo(() => (documentData ? createPlugins(documentData) : []), [documentData]);

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
										<div className='flex h-full flex-col'>
											<div className='mb-2 flex shrink-0 flex-col gap-2 border-b py-2 sm:flex-row sm:items-center sm:justify-evenly'>
												<ZoomToolbar documentId={activeDocumentId} />
												<PageNavigation bookId={bookId} documentId={activeDocumentId} initialPage={initialPage} />
											</div>
											<div className='min-h-0 flex-1 overflow-hidden'>
												<PdfViewport documentId={activeDocumentId} />
											</div>
										</div>
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
