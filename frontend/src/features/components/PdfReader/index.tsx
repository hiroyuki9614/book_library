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
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from 'react';
import { fetchBookFile } from '@/api/books';
import { fetchReadingInfo, type ReadingInfo } from '@/api/readingInfo';
import ReadingSaveStatus from '../ReadingSaveStatus';
import { useReadingSave } from '@/hooks/useReadingSave';

export type AppState = GlobalStoreState<{
	[SCROLL_PLUGIN_ID]: ScrollState;
}>;

type PageTurnHandler = { documentId: string; turnPage: (delta: number) => void; getPage: () => number };

function createCommands(direction: PageTurnDirection, getPageTurnHandler: () => PageTurnHandler | null): Record<string, Command<AppState>> { return {
	'nav.previous': {
		id: 'nav.previous',
		label: direction === 'rtl' ? '>' : '<',
		shortcuts: [direction === 'rtl' ? 'arrowright' : 'arrowleft', 'k'],
		action: ({ documentId }) => {
			const handler = getPageTurnHandler();
			if (handler?.documentId === documentId) handler.turnPage(-1);
		},
		disabled: ({ state, documentId }) => {
			const scrollState = state.plugins.scroll.documents[documentId];
			const handler = getPageTurnHandler();
			const page = handler?.documentId === documentId ? handler.getPage() : scrollState?.currentPage;
			return page === undefined || page <= 1;
		},
	},
	'nav.next': {
		id: 'nav.next',
		label: direction === 'rtl' ? '<' : '>',
		shortcuts: [direction === 'rtl' ? 'arrowleft' : 'arrowright', 'j'],
		action: ({ documentId }) => {
			const handler = getPageTurnHandler();
			if (handler?.documentId === documentId) handler.turnPage(1);
		},
		disabled: ({ state, documentId }) => {
			const scrollState = state.plugins.scroll.documents[documentId];
			const handler = getPageTurnHandler();
			const page = handler?.documentId === documentId ? handler.getPage() : scrollState?.currentPage;
			return !scrollState || page === undefined || page >= scrollState.totalPages;
		},
	},
}; }

function createPlugins(documentData: ArrayBuffer, direction: PageTurnDirection, getPageTurnHandler: () => PageTurnHandler | null) {
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
			commands: createCommands(direction, getPageTurnHandler),
		}),
	];
}

type PageNavigationProps = { bookId: number; documentId: string; initialPage: number; initialReadingInfo: ReadingInfo | null; pageTurnDirection: PageTurnDirection; pageTurnRef: RefObject<PageTurnHandler | null> };

type PdfNavigationState =
	| { phase: 'waiting-layout'; queuedPage: number | null }
	| { phase: 'navigating'; pageNumber: number; initial: boolean; queuedPage: number | null }
	| { phase: 'ready' };

function getNavigationPage(navigation: PdfNavigationState, currentPage: number) {
	if (navigation.phase === 'ready') return currentPage;
	if (navigation.queuedPage !== null) return navigation.queuedPage;
	if (navigation.phase === 'navigating' && !navigation.initial) return navigation.pageNumber;
	return currentPage;
}

function getPdfProgressPercentage(currentPage: number, totalPages: number) {
	if (!Number.isSafeInteger(currentPage) || !Number.isSafeInteger(totalPages) || currentPage < 1 || totalPages < 1 || currentPage > totalPages) return null;
	return Math.round((currentPage / totalPages) * 100);
}

const usePageNavigation = ({ bookId, documentId, initialPage, initialReadingInfo, pageTurnDirection, pageTurnRef, userId }: PageNavigationProps & { userId?: number }) => {
	const { state } = useScroll(documentId);
	const [pageInputOverride, setPageInputOverride] = useState<string | null>(null);
	const pageInput = pageInputOverride ?? String(state.currentPage);
	const { provides: scrollCapability } = useScrollCapability();
	const [navigation, setNavigation] = useState<PdfNavigationState>({ phase: 'waiting-layout', queuedPage: null });
	const navigationRef = useRef(navigation);
	const lastAcceptedPage = useRef<number | null>(null);
	const lateRestorePage = useRef<number | null>(null);
	const { save, status, retry, confirmInitialStatus } = useReadingSave(bookId, userId);
	const previousCommand = useCommand('nav.previous', documentId);
	const nextCommand = useCommand('nav.next', documentId);
	const navigationPage = getNavigationPage(navigation, state.currentPage);

	const updateNavigation = useCallback((next: PdfNavigationState) => {
		navigationRef.current = next;
		setNavigation(next);
	}, []);

	const dispatchPage = useCallback((pageNumber: number, initial = false, queuedPage: number | null = null) => {
		if (!scrollCapability) return;
		// An explicit return to the restored page is a new intent, not an echo.
		if (!initial && pageNumber === lateRestorePage.current) lateRestorePage.current = null;
		updateNavigation({ phase: 'navigating', pageNumber, initial, queuedPage });
		scrollCapability.forDocument(documentId).scrollToPage({ pageNumber, behavior: 'auto' });
	}, [documentId, scrollCapability, updateNavigation]);

	useEffect(() => {
		if (initialReadingInfo) confirmInitialStatus(initialReadingInfo);
	}, [confirmInitialStatus, initialReadingInfo]);

	useEffect(() => {
		if (!scrollCapability) return;
		let active = true;
		// LayoutReady is replayed to late subscribers and carries the real page count.
		// useScroll's initial 1/1 state is not evidence that a document is ready.
		const unsubscribe = scrollCapability.onLayoutReady((event) => {
			const pending = navigationRef.current;
			if (!active || event.documentId !== documentId || pending.phase !== 'waiting-layout') return;
			const page = Math.max(1, Math.min(initialPage, event.totalPages));
			dispatchPage(page, true, pending.queuedPage);
		});
		return () => {
			active = false;
			unsubscribe();
		};
	}, [dispatchPage, documentId, initialPage, scrollCapability]);

	useEffect(() => {
		if (navigationRef.current !== navigation || navigation.phase === 'waiting-layout') return;
		const progressPercentage = getPdfProgressPercentage(state.currentPage, state.totalPages);
		if (progressPercentage === null) return;
		if (navigation.phase === 'navigating') {
			if (state.currentPage !== navigation.pageNumber) return;
			// Only one command is in flight. A newer intent replaces the queued
			// target, and the superseded acknowledgement never reaches the save hook.
			if (navigation.queuedPage !== null) {
				if (navigation.initial) lateRestorePage.current = navigation.pageNumber;
				const queuedPage = navigation.queuedPage;
				let active = true;
				// Dispatch after the synchronous effect phase; cancel when replaced or unmounted.
				queueMicrotask(() => {
					if (active && navigationRef.current === navigation) dispatchPage(queuedPage);
				});
				return () => { active = false; };
			}
			lastAcceptedPage.current = state.currentPage;
			// A renderer acknowledgement is observed in this effect. Reconcile UI state asynchronously.
			queueMicrotask(() => {
				if (navigationRef.current === navigation) updateNavigation({ phase: 'ready' });
			});
			if (navigation.initial) return;
		} else {
			// Native scrolling has no queued command. Accept changed pages after
			// restoration, but not a late restore echo after a queued explicit jump.
			if (state.currentPage === lastAcceptedPage.current || state.currentPage === lateRestorePage.current) return;
			lastAcceptedPage.current = state.currentPage;
		}
		const readStatus = state.currentPage >= state.totalPages ? 'completed' : 'reading';
		save(state.currentPage, readStatus, progressPercentage);
	}, [dispatchPage, navigation, state.currentPage, state.totalPages, save, updateNavigation]);

	const requestPage = useCallback((pageNumber: number) => {
		if (!Number.isSafeInteger(pageNumber) || pageNumber < 1 || pageNumber > state.totalPages) return;
		const pending = navigationRef.current;
		if (pending.phase === 'ready') dispatchPage(pageNumber);
		else updateNavigation({ ...pending, queuedPage: pageNumber });
		setPageInputOverride(null);
	}, [dispatchPage, state.totalPages, updateNavigation]);

	const getPage = useCallback(() => {
		const pending = navigationRef.current;
		// Use the last acknowledged reader page for an already-ready command.
		// A stale viewport observation must not send a turn from an older page.
		return pending.phase === 'ready' ? (lastAcceptedPage.current ?? state.currentPage) : getNavigationPage(pending, state.currentPage);
	}, [state.currentPage]);
	const turnPage = useCallback((delta: number) => {
		const currentPage = getPage();
		const pageNumber = Math.max(1, Math.min(state.totalPages, currentPage + delta));
		if (pageNumber !== currentPage) requestPage(pageNumber);
	}, [getPage, requestPage, state.totalPages]);
	const handleNativeScrollIntent = useCallback(() => {
		// Actual wheel/drag/touch motion overrides a pending programmatic jump.
		// Otherwise an interrupted restore could strand navigation in "navigating"
		// and ignore every new renderer page observation until the old target lands.
		const pending = navigationRef.current;
		if (pending.phase === 'ready') {
			lateRestorePage.current = null;
			return;
		}
		if (pending.phase !== 'navigating') return;
		// A renderer may already show the explicit target while its navigation
		// acknowledgement effect is still pending. Persist that reached position
		// once; initial restore itself must never issue a PATCH.
		const targetReached = state.currentPage === pending.pageNumber;
		if (targetReached && !pending.initial && lastAcceptedPage.current !== state.currentPage) {
			const percentage = getPdfProgressPercentage(state.currentPage, state.totalPages);
			if (percentage !== null) {
				const readStatus = state.currentPage >= state.totalPages ? 'completed' : 'reading';
				save(state.currentPage, readStatus, percentage);
			}
		}
		// A stale command may still emit the interrupted target later. Suppress
		// that echo only when it has not already been observed on screen.
		lateRestorePage.current = targetReached ? null : pending.pageNumber;
		lastAcceptedPage.current = state.currentPage;
		updateNavigation({ phase: 'ready' });
	}, [save, state.currentPage, state.totalPages, updateNavigation]);

	useEffect(() => {
		const handler = { documentId, turnPage, getPage };
		pageTurnRef.current = handler;
		return () => {
			if (pageTurnRef.current === handler) pageTurnRef.current = null;
		};
	}, [documentId, getPage, pageTurnRef, turnPage]);

	const handleGoToPage = (e: React.FormEvent<HTMLFormElement>) => {
		e.preventDefault();
		requestPage(Number(pageInput));
	};

	return {
		state,
		pageInput,
		setPageInput: setPageInputOverride,
		handleGoToPage,
		previousCommand: previousCommand && { ...previousCommand, disabled: navigationPage <= 1 },
		nextCommand: nextCommand && { ...nextCommand, disabled: navigationPage >= state.totalPages },
		requestPage,
		turnPage,
		handleNativeScrollIntent,
		status,
		retry,
		pageTurnDirection,
	};
};

type PageNavigationModel = ReturnType<typeof usePageNavigation>;

const PageNavigation = ({ navigation }: { navigation: PageNavigationModel }) => (
	<CommandButton
		pageTurnDirection={navigation.pageTurnDirection}
		prevResolved={navigation.previousCommand}
		nextResolved={navigation.nextCommand}
		state={navigation.state}
		pageInput={navigation.pageInput}
		setPageInput={navigation.setPageInput}
		handleGoToPage={navigation.handleGoToPage}
	/>
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

function PdfTableOfContents({ documentId, open, onOpenChange, onJump, inline = false }: { documentId: string; open: boolean; onOpenChange: (open: boolean) => void; onJump: (page: number) => void; inline?: boolean }) {
	const { provides: bookmarksCapability } = useBookmarkCapability();
	const { state } = useScroll(documentId);
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
		onJump(Math.max(1, Math.min(page, state.totalPages)));
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

const PdfViewport = ({ documentId, pageTurnDirection, navigationDisabled, onTurn, onNativeScrollIntent }: { documentId: string; pageTurnDirection: PageTurnDirection; navigationDisabled: boolean; onTurn: (delta: number) => void; onNativeScrollIntent: () => void }) => {
	const containerRef = useRef<HTMLDivElement | null>(null);
	const [hasRenderedPage, setHasRenderedPage] = useState(false);
	const [copyMode, setCopyMode] = useState(false);
	const [longPressMenuOpen, setLongPressMenuOpen] = useState(false);
	const [longPressPoint, setLongPressPoint] = useState({ x: 0, y: 0 });
	const [copyStatus, setCopyStatus] = useState<'idle' | 'copying' | 'copied' | 'unavailable' | 'failed'>('idle');
	const { provides: scroll } = useScroll(documentId);
	const { provides: selection } = useSelectionCapability();
	const longPressTimer = useRef<number | null>(null);
	const pointerIds = useRef(new Set<number>());
	const pointerStart = useRef<{ id: number; x: number; y: number } | null>(null);
	useEffect(() => {
		const container = containerRef.current;
		if (!container || !scroll) return;
		return attachEdgeTapNavigation(container, {
			direction: pageTurnDirection,
			isDisabled: () => navigationDisabled || copyMode || longPressMenuOpen,
			getBounds: () => {
				const bounds = container.getBoundingClientRect();
				let page = container.querySelector<HTMLElement>('[data-pdf-page]');
				while (page && page !== container) {
					const style = getComputedStyle(page);
					if ((style.overflowX === 'auto' || style.overflowX === 'scroll' || style.overflowY === 'auto' || style.overflowY === 'scroll') && page.scrollHeight > page.clientHeight) {
						return { left: bounds.left, width: Math.min(bounds.width, page.clientWidth) };
					}
					page = page.parentElement;
				}
				return { left: bounds.left, width: bounds.width };
			},
			getSelectionText: () => window.getSelection()?.toString() ?? '',
			onTurn: (action) => onTurn(action === 'next' ? 1 : -1),
		});
	}, [copyMode, longPressMenuOpen, navigationDisabled, onTurn, pageTurnDirection, scroll]);

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
			if (Math.hypot(event.clientX - start.x, event.clientY - start.y) >= 10) {
				cancelLongPress();
				onNativeScrollIntent();
			}
		};
		const onPointerEnd = (event: PointerEvent) => {
			activePointerIds.delete(event.pointerId);
			cancelLongPress();
		};
		const onWheel = (event: WheelEvent) => {
			cancelLongPress();
			if (!event.ctrlKey && (event.deltaX !== 0 || event.deltaY !== 0)) onNativeScrollIntent();
		};
		const onTouchStart = (event: TouchEvent) => { if (event.touches.length > 1) cancelLongPress(); };
		const onTouchMove = (event: TouchEvent) => { if (event.touches.length === 1) onNativeScrollIntent(); };
		container.addEventListener('pointerdown', onPointerDown);
		window.addEventListener('pointermove', onPointerMove, { capture: true, passive: true });
		// Edge navigation observes pointerup in window capture and may stop it
		// before EmbedPDF's page layer receives it. Keep long-press cleanup on the
		// same target so a short edge tap cannot leave its timer armed.
		window.addEventListener('pointerup', onPointerEnd, { capture: true });
		window.addEventListener('pointercancel', onPointerEnd, { capture: true });
		container.addEventListener('wheel', onWheel, { passive: true });
		container.addEventListener('touchstart', onTouchStart, { passive: true });
		container.addEventListener('touchmove', onTouchMove, { passive: true });
		return () => {
			container.removeEventListener('pointerdown', onPointerDown);
			window.removeEventListener('pointermove', onPointerMove, { capture: true });
			window.removeEventListener('pointerup', onPointerEnd, { capture: true });
			window.removeEventListener('pointercancel', onPointerEnd, { capture: true });
			container.removeEventListener('wheel', onWheel);
			container.removeEventListener('touchstart', onTouchStart);
			container.removeEventListener('touchmove', onTouchMove);
			activePointerIds.clear();
			cancelLongPress();
		};
	}, [cancelLongPress, copyMode, longPressMenuOpen, onNativeScrollIntent]);

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

function ReaderWorkspace({ bookId, bookTitle, documentId, initialPage, initialReadingInfo, pageTurnDirection, pageTurnRef, userId, restoreError, mobileMenuOpen, onMobileMenuChange }: { bookId: number; bookTitle?: string; documentId: string; initialPage: number; initialReadingInfo: ReadingInfo | null; pageTurnDirection: PageTurnDirection; pageTurnRef: RefObject<PageTurnHandler | null>; userId?: number; restoreError: boolean; mobileMenuOpen: boolean; onMobileMenuChange: (open: boolean) => void }) {
	const [tocOpen, setTocOpen] = useState(false);
	const [mobileTocOpen, setMobileTocOpen] = useState(false);
	const [settingsOpen, setSettingsOpen] = useState(false);
	const navigation = usePageNavigation({ bookId, documentId, initialPage, initialReadingInfo, pageTurnDirection, pageTurnRef, userId });
	return (
		<div className='flex h-full flex-col'>
			<header className='flex shrink-0 items-center gap-1 border-b px-1 py-1 sm:gap-2 sm:px-3 sm:py-2 xl:py-[3.5px]'>
				<Link to='/' className='inline-flex min-h-11 shrink-0 items-center rounded-md px-2 text-sm text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'>本棚に戻る</Link>
				<span className='min-w-0 flex-1 truncate px-1 text-center text-sm font-medium text-foreground'>{bookTitle || 'PDFリーダー'}</span>
				<div className='max-w-[7rem] shrink md:max-w-none'><ReadingSaveStatus status={navigation.status} retry={navigation.retry} /></div>
				<div className='hidden shrink-0 items-center gap-2 md:flex'>
					<PdfTableOfContents documentId={documentId} open={tocOpen} onOpenChange={setTocOpen} onJump={navigation.requestPage} />
					<Sheet open={settingsOpen} onOpenChange={setSettingsOpen}>
						<SheetTrigger asChild><Button type='button' variant='outline' className='min-h-11 px-3'>表示設定</Button></SheetTrigger>
						<SheetContent side='right' className='w-[min(88vw,22rem)] [&>button]:size-11'>
							<SheetHeader><SheetTitle>表示設定</SheetTitle><SheetDescription>PDFの拡大率を調整します。</SheetDescription></SheetHeader>
							<div className='pt-4'><ZoomToolbar documentId={documentId} compact /></div>
						</SheetContent>
					</Sheet>
				</div>
				<Button type='button' variant='outline' className='min-h-11 shrink-0 px-3 md:hidden' aria-label='操作メニューを開く' onClick={() => onMobileMenuChange(true)}>メニュー</Button>
			</header>
			{restoreError && <p role='alert' className='shrink-0 px-3 py-1 text-xs'>保存位置を取得できませんでした。先頭から表示しています。</p>}
			<Sheet open={mobileMenuOpen} onOpenChange={onMobileMenuChange}>
				<SheetContent side='right' className='w-[min(88vw,22rem)] p-0 duration-[240ms] motion-reduce:duration-0 [&>button]:size-11'>
					<SheetHeader className='shrink-0 border-b pr-16'>
						<SheetTitle>操作メニュー</SheetTitle>
						<SheetDescription>拡大・ページ移動・目次</SheetDescription>
					</SheetHeader>
					<div className='no-scrollbar min-h-0 flex-1 overflow-y-auto p-4'>
						<div className='flex flex-col gap-3'>
							<section aria-label='表示設定'><h3 className='mb-2 font-medium'>表示設定</h3><ZoomToolbar documentId={documentId} compact /></section>
							<PageNavigation navigation={navigation} />
							<Button type='button' variant='outline' className='min-h-11 w-full justify-start' aria-expanded={mobileTocOpen} onClick={() => setMobileTocOpen((open) => !open)}>目次</Button>
							{mobileTocOpen && <PdfTableOfContents inline documentId={documentId} open={mobileTocOpen} onOpenChange={setMobileTocOpen} onJump={navigation.requestPage} />}
						</div>
					</div>
				</SheetContent>
			</Sheet>
			<main tabIndex={0} className='relative min-h-0 flex-1 overflow-hidden bg-white outline-none' onKeyDown={(event) => {
				if (['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(event.key)) navigation.handleNativeScrollIntent();
			}}>
				<PdfViewport documentId={documentId} pageTurnDirection={pageTurnDirection} navigationDisabled={tocOpen || mobileMenuOpen} onTurn={navigation.turnPage} onNativeScrollIntent={navigation.handleNativeScrollIntent} />
			</main>
			<footer data-testid='pdf-reader-footer' aria-hidden={mobileMenuOpen ? true : undefined} inert={mobileMenuOpen || undefined} className='flex shrink-0 flex-wrap items-center justify-center gap-x-3 gap-y-1 border-t bg-white px-2 py-1 sm:px-4'>
				<div className='hidden md:block'><ZoomToolbar documentId={documentId} /></div>
				<PageNavigation navigation={navigation} />
			</footer>
		</div>
	);
}

function PdfReaderSession({ bookId, bookTitle, pageTurnDirection = 'ltr', userId }: { bookId: number; bookTitle?: string; pageTurnDirection?: PageTurnDirection; userId?: number }) {
	const [documentData, setDocumentData] = useState<ArrayBuffer | null>(null);
	const [fileError, setFileError] = useState(false);
	const [restoreError, setRestoreError] = useState(false);
	const [initialPage, setInitialPage] = useState<number | null>(null);
	const [initialReadingInfo, setInitialReadingInfo] = useState<ReadingInfo | null>(null);
	const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
	// Stable mutable command bridge: plugin definitions are constructed during render,
	// so do not pass a React ref through the render-phase plugin factory.
	const [pageTurnBridge] = useState<{ current: PageTurnHandler | null }>(() => ({ current: null }));
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

		const readingInfoRequest = userId === undefined ? fetchReadingInfo(bookId) : fetchReadingInfo(bookId, userId);
		void readingInfoRequest
			.then((readingInfo) => {
				if (!active) return;
				setInitialReadingInfo(readingInfo);
				setInitialPage(readingInfo.currentPage);
			})
			.catch(() => {
				if (active) {
					setInitialReadingInfo(null);
					setInitialPage(1);
					setRestoreError(true);
				}
			});

		return () => {
			active = false;
		};
	}, [bookId, userId]);

	const getPageTurnHandler = useCallback(() => pageTurnBridge.current, [pageTurnBridge]);
	const plugins = useMemo(() => (documentData ? createPlugins(documentData, pageTurnDirection, getPageTurnHandler) : []), [documentData, pageTurnDirection, getPageTurnHandler]);

	if (fileError || engineError) return <p role='alert'>PDFファイルまたは描画エンジンを読み込めませんでした。</p>;

	if (isLoading || !engine || !documentData || initialPage === null) {
		return (
			<div className='flex h-[100dvh] items-center justify-center bg-white text-muted-foreground' role='status'>
				<span className='text-sm'>PDFリーダーを読み込んでいます…</span>
			</div>
		);
	}

	return (
		<div className='flex h-[100dvh] flex-col bg-white text-foreground'>
			<div className='min-h-0 flex-1'>
				<EmbedPDF engine={engine} plugins={plugins}>
					{({ activeDocumentId }) =>
						activeDocumentId && (
							<DocumentContent documentId={activeDocumentId}>
								{({ isLoaded }) =>
									isLoaded && (
										<ReaderWorkspace key={activeDocumentId} bookId={bookId} bookTitle={bookTitle} documentId={activeDocumentId} initialPage={initialPage} initialReadingInfo={initialReadingInfo} pageTurnDirection={pageTurnDirection} pageTurnRef={pageTurnBridge} userId={userId} restoreError={restoreError} mobileMenuOpen={mobileMenuOpen} onMobileMenuChange={setMobileMenuOpen} />
									)
								}
							</DocumentContent>
						)
					}
				</EmbedPDF>
			</div>
		</div>
	);
}

function PdfReader(props: { bookId: number; bookTitle?: string; pageTurnDirection?: PageTurnDirection; userId?: number }) {
	const readerKey = `${props.bookId}:${props.userId === undefined ? 'anonymous' : props.userId}`;
	return <PdfReaderSession key={readerKey} {...props} />;
}

export default PdfReader;
