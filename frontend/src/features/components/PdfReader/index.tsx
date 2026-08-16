import { createPluginRegistration } from '@embedpdf/core';
import { EmbedPDF } from '@embedpdf/core/react';
import { usePdfiumEngine } from '@embedpdf/engines/react';
import { Link } from 'react-router-dom';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from '@/components/ui/sheetCapture';
import { Button } from '@/components/ui/button';
import { ZoomToolbar } from './ZoomToolbar';
import CommandButton from './Command';
import { useCommand } from '@embedpdf/plugin-commands/react';

// Import the essential plugins
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
	// [ZOOM_PLUGIN_ID]: ZoomState;
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
	const [pageInput, setPageInput] = useState(String(state.currentPage));
	const [isReadyToPersist, setIsReadyToPersist] = useState(false);
	const targetPage = Math.min(initialPage, state.totalPages);
	const lastPersistedPage = useRef<number | null>(null);

	const previousCommand = useCommand('nav.previous', documentId);
	const nextCommand = useCommand('nav.next', documentId);

	useEffect(() => {
		if (!scroll || state.totalPages < 1 || lastPersistedPage.current === targetPage) {
			return;
		}

		lastPersistedPage.current = targetPage;
		setIsReadyToPersist(false);
		scroll.scrollToPage({ pageNumber: targetPage });
	}, [scroll, state.totalPages, targetPage]);

	useEffect(() => {
		if (!isReadyToPersist) {
			if (state.currentPage === targetPage) {
				setIsReadyToPersist(true);
			}
			return;
		}

		if (state.currentPage === lastPersistedPage.current) {
			return;
		}

		lastPersistedPage.current = state.currentPage;
		void saveReadingInfo(bookId, state.currentPage, state.totalPages).catch(() => undefined);
	}, [bookId, isReadyToPersist, state.currentPage, targetPage]);

	const handleGoToPage = (e: React.FormEvent<HTMLFormElement>) => {
		e.preventDefault();
		const pageNumber = parseInt(pageInput, 10);
		if (pageNumber >= 1 && pageNumber <= state.totalPages) {
			scroll?.scrollToPage({ pageNumber });
		}
	};

	return (
		<>
			<CommandButton prevResolved={previousCommand} nextResolved={nextCommand} state={state} pageInput={pageInput} setPageInput={setPageInput} handleGoToPage={handleGoToPage} />
		</>
	);
};

function PdfReader({ bookId }: { bookId: number }) {
	const [documentUrl, setDocumentUrl] = useState<string | null>(null);
	const [fileError, setFileError] = useState(false);
	const [initialPage, setInitialPage] = useState<number | null>(null);
	const { engine, isLoading } = usePdfiumEngine();

	useEffect(() => {
		let objectUrl: string | undefined;
		setDocumentUrl(null);
		setFileError(false);
		setInitialPage(null);

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
			if (objectUrl) {
				URL.revokeObjectURL(objectUrl);
			}
		};
	}, [bookId]);

	const plugins = useMemo(() => (documentUrl ? createPlugins(documentUrl) : []), [documentUrl]);

	if (fileError) {
		return <p>PDFファイルを取得できませんでした。</p>;
	}

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
		<div className='flex h-screen flex-col bg-background text-foreground'>
			<header className='flex items-center justify-between p-3'>
				<div>
					<Link to='/' className='text-sm text-primary'>
						本棚に戻る
					</Link>
				</div>
				<div>
					<Sheet>
						<SheetTrigger asChild>
							<Button variant='outline' className='bg-white active:bg-white focus:bg-white data-[state=open]:bg-white'>
								目次
							</Button>
						</SheetTrigger>
						<SheetContent side='right' className='data-[side=bottom]:max-h-[50vh] data-[side=top]:max-h-[50vh]'>
							<SheetHeader>
								<SheetTitle>この書籍の目次</SheetTitle>
							</SheetHeader>
							<div className='no-scrollbar overflow-y-auto px-4'></div>
						</SheetContent>
					</Sheet>
				</div>
			</header>
			<main tabIndex={0} className='relative h-[calc(100vh-73px)] overflow-hidden outline-none bg-white px-4'>
				<EmbedPDF engine={engine} plugins={plugins}>
					{({ activeDocumentId }) =>
						activeDocumentId && (
							<DocumentContent documentId={activeDocumentId}>
								{({ isLoaded }) =>
									isLoaded && (
										<div style={{ display: 'flex', height: '100%', flexDirection: 'column' }}>
											<div className='mb-2 flex items-center gap-4 justify-evenly'>
												<ZoomToolbar documentId={activeDocumentId} />
																											<PageNavigation bookId={bookId} documentId={activeDocumentId} initialPage={initialPage} />
											</div>
											<div style={{ flex: 1, overflow: 'hidden' }}>
												<Viewport documentId={activeDocumentId}>
													<Scroller
														documentId={activeDocumentId}
														renderPage={({ width, height, pageIndex }) => (
															<PagePointerProvider documentId={activeDocumentId} pageIndex={pageIndex}>
																<div style={{ width, height, position: 'relative' }}>
																	<RenderLayer documentId={activeDocumentId} pageIndex={pageIndex} style={{ pointerEvents: 'none' }} />
																	<SelectionLayer documentId={activeDocumentId} pageIndex={pageIndex} />
																</div>
															</PagePointerProvider>
														)}
													/>
												</Viewport>
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
