import { createPluginRegistration } from '@embedpdf/core';
import { EmbedPDF } from '@embedpdf/core/react';
import { usePdfiumEngine } from '@embedpdf/engines/react';
import { Link } from 'react-router-dom';
import { Spinner } from '@/components/ui/spinner';
import { ZoomToolbar } from './ZoomToolbar';
import CommandButton from './Command';
import { useCommand } from '@embedpdf/plugin-commands/react';

import { Viewport, ViewportPluginPackage } from '@embedpdf/plugin-viewport/react';
import { Scroller, ScrollPluginPackage, ScrollStrategy, useScroll } from '@embedpdf/plugin-scroll/react';
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
import { fetchReadingInfo, saveReadingInfo } from '@/api/readingInfo';

export type AppState = GlobalStoreState<{
	[SCROLL_PLUGIN_ID]: ScrollState;
}>;

const myCommands: Record<string, Command<AppState>> = {
	'nav.previous': {
		id: 'nav.previous',
		label: '<',
		shortcuts: ['arrowleft', 'k'],
		action: ({ registry }) => {
			const scrollPlugin = registry.getPlugin('scroll');
			const scroll = scrollPlugin?.provides?.();

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
		action: ({ registry }) => {
			const scrollPlugin = registry.getPlugin('scroll');
			const scroll = scrollPlugin?.provides?.();

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

function createPlugins(documentUrl: string) {
	return [
		createPluginRegistration(DocumentManagerPluginPackage, {
			initialDocuments: [{ url: documentUrl }],
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
	const isReadyToPersistRef = useRef(false);
	const targetPage = Math.min(initialPage, state.totalPages);
	const lastPersistedPage = useRef<number | null>(null);

	const previousCommand = useCommand('nav.previous', documentId);
	const nextCommand = useCommand('nav.next', documentId);

	useEffect(() => {
		if (!scroll || state.totalPages < 1 || lastPersistedPage.current === targetPage) {
			return;
		}

		lastPersistedPage.current = targetPage;
		isReadyToPersistRef.current = false;
		scroll.scrollToPage({ pageNumber: targetPage });
	}, [scroll, state.totalPages, targetPage]);

	useEffect(() => {
		if (!isReadyToPersistRef.current) {
			if (state.currentPage === targetPage) {
				isReadyToPersistRef.current = true;
			}
			return;
		}

		if (state.currentPage === lastPersistedPage.current) {
			return;
		}

		lastPersistedPage.current = state.currentPage;
		const readStatus = state.currentPage >= state.totalPages ? 'completed' : 'reading';
		void saveReadingInfo(bookId, state.currentPage, readStatus).catch(() => undefined);
	}, [bookId, state.currentPage, state.totalPages, targetPage]);

	const handleGoToPage = (e: React.FormEvent<HTMLFormElement>) => {
		e.preventDefault();
		const pageNumber = parseInt(pageInput, 10);
		if (pageNumber >= 1 && pageNumber <= state.totalPages) {
			scroll?.scrollToPage({ pageNumber });
			setPageInputOverride(null);
		}
	};

	return <CommandButton prevResolved={previousCommand} nextResolved={nextCommand} state={state} pageInput={pageInput} setPageInput={setPageInputOverride} handleGoToPage={handleGoToPage} />;
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
				<div className='pointer-events-none absolute inset-0 z-10 flex items-center justify-center bg-white/90' role='status' aria-live='polite'>
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
							<div style={{ width, height, position: 'relative' }}>
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
	const [documentUrl, setDocumentUrl] = useState<string | null>(null);
	const [fileError, setFileError] = useState(false);
	const [initialPage, setInitialPage] = useState<number | null>(null);
	const { engine, isLoading } = usePdfiumEngine();

	useEffect(() => {
		let objectUrl: string | undefined;

		void fetchBookFile(bookId)
			.then((blob) => {
				objectUrl = URL.createObjectURL(blob);
				setDocumentUrl(objectUrl);
			})
			.catch(() => setFileError(true));

		void fetchReadingInfo(bookId)
			.then((readingInfo) => setInitialPage(readingInfo.currentPage))
			.catch(() => setInitialPage(1));

		return () => {
			if (objectUrl) URL.revokeObjectURL(objectUrl);
		};
	}, [bookId]);

	const plugins = useMemo(() => (documentUrl ? createPlugins(documentUrl) : []), [documentUrl]);

	if (fileError) return <p>PDFファイルを取得できませんでした。</p>;

	if (isLoading || !engine || !documentUrl || initialPage === null) {
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
				<Link to='/' className='inline-flex min-h-11 items-center rounded-md px-2 text-sm text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'>本棚に戻る</Link>
				<span className='ml-auto pr-2 text-sm font-medium text-muted-foreground'>PDFリーダー</span>
			</header>
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
