import { createPluginRegistration } from '@embedpdf/core';
import { EmbedPDF } from '@embedpdf/core/react';
import { usePdfiumEngine } from '@embedpdf/engines/react';
import { Link } from 'react-router-dom';
import { Sheet, SheetClose, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle, SheetTrigger } from '@/components/ui/sheetCapture';
import { Button } from '@/components/ui/button';
import { ZoomToolbar } from './ZoomToolbar';
import { useEffect, useState } from 'react';
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
import { GlobalStoreState } from '@embedpdf/core';
import { Command } from '@embedpdf/plugin-commands';
import { SCROLL_PLUGIN_ID, ScrollState } from '@embedpdf/plugin-scroll';

import { SelectionLayer, SelectionPluginPackage } from '@embedpdf/plugin-selection/react';

export type AppState = GlobalStoreState<{
	[SCROLL_PLUGIN_ID]: ScrollState;
	// [ZOOM_PLUGIN_ID]: ZoomState;
}>;

const myCommands: Record<string, Command<AppState>> = {
	'nav.previous': {
		id: 'nav.previous',
		label: 'Previous Page',
		shortcuts: ['arrowleft', 'k'],
		action: ({ registry }) => {
			registry.getPlugin('scroll')?.provides()?.scrollToPreviousPage();
		},
		disabled: ({ state, documentId }) => {
			const scrollState = state.plugins.scroll.documents[documentId];
			return scrollState ? scrollState.currentPage <= 1 : true;
		},
	},
	'nav.next': {
		id: 'nav.next',
		label: 'Next Page',
		shortcuts: ['arrowright', 'j'],
		action: ({ registry }) => {
			registry.getPlugin('scroll')?.provides()?.scrollToNextPage();
		},
		// The 'state' argument is now typed as AppState
		disabled: ({ state, documentId }) => {
			// Access specific document state safely
			const scrollState = state.plugins.scroll.documents[documentId];
			return scrollState ? scrollState.currentPage >= scrollState.totalPages : true;
		},
	},
};

// 1. Register the plugins you need
const plugins = [
	createPluginRegistration(DocumentManagerPluginPackage, {
		initialDocuments: [{ url: 'https://snippet.embedpdf.com/ebook.pdf' }],
	}),
	createPluginRegistration(ViewportPluginPackage),
	createPluginRegistration(ScrollPluginPackage),
	createPluginRegistration(RenderPluginPackage),
	createPluginRegistration(InteractionManagerPluginPackage),
	createPluginRegistration(SelectionPluginPackage),
	createPluginRegistration(ZoomPluginPackage, {
		defaultZoomLevel: ZoomMode.FitPage,
	}),
	createPluginRegistration(ScrollPluginPackage, {
		defaultStrategy: ScrollStrategy.Vertical, // or Horizontal
		defaultPageGap: 10,
	}),
	createPluginRegistration(CommandsPluginPackage, {
		commands: myCommands, // See "Defining Commands" below
	}),
];

const PageNavigation = ({ documentId }: { documentId: string }) => {
	const { provides: scroll, state } = useScroll(documentId);
	const [pageInput, setPageInput] = useState(String(state.currentPage));

	const previousCommand = useCommand('nav.previous', documentId);
	const nextCommand = useCommand('nav.next', documentId);

	useEffect(() => {
		setPageInput(String(state.currentPage));
	}, [state.currentPage]);

	const handleGoToPage = (e: React.FormEvent<HTMLFormElement>) => {
		e.preventDefault();
		const pageNumber = parseInt(pageInput, 10);
		if (pageNumber >= 1 && pageNumber <= state.totalPages) {
			scroll?.scrollToPage({ pageNumber });
		}
	};

	return (
		<div className='flex items-center justify-center gap-2 border-b border-gray-300 bg-gray-100 px-3 py-2 dark:border-gray-700 dark:bg-gray-800'>
			{/* Previous button */}
			<CommandButton prevResolved={previousCommand} nextResolved={nextCommand} state={state} pageInput={pageInput} setPageInput={setPageInput} handleGoToPage={handleGoToPage} />
		</div>
	);
};

function PdfReader() {
	// 2. Initialize the engine with the React hook
	const { engine, isLoading } = usePdfiumEngine();

	// 3. Wrap your UI with the <EmbedPDF> provider
	if (isLoading || !engine) {
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
											<ZoomToolbar documentId={activeDocumentId} /> {/* 2. Add the component here with documentId */}
											<PageNavigation documentId={activeDocumentId} />
											<div style={{ flex: 1, overflow: 'hidden' }}>
												<Viewport documentId={activeDocumentId}>
													<Scroller
														documentId={activeDocumentId}
														renderPage={({ width, height, pageIndex, scale }) => (
															<PagePointerProvider documentId={activeDocumentId} pageIndex={pageIndex}>
																<div style={{ width, height, position: 'relative' }}>
																	<RenderLayer documentId={activeDocumentId} pageIndex={pageIndex} style={{ pointerEvents: 'none' }} scale={scale} />
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
