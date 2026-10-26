// TODO: 階層付きTOC(subitems)対応

import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import ePub from 'epubjs';
import { fetchBookFile } from '@/api/books';
import { fetchReadingInfo, saveReadingInfo } from '@/api/readingInfo';
import { Field, FieldLabel } from '@/components/ui/field';
import { Progress } from '@/components/ui/progress';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Spinner } from '@/components/ui/spinner';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from '@/components/ui/sheetCapture';

type TocItem = {
	href: string;
	label: string;
};

type RelocatedLocation = {
	start: { cfi: string };
};

type EpubRenditionEventHandler = {
	(event: 'relocated', handler: (value: RelocatedLocation) => void): void;
	(event: 'keyup', handler: (value: KeyboardEvent) => void): void;
};

type EpubRenditionRuntime = {
	on: EpubRenditionEventHandler;
	off?: EpubRenditionEventHandler;
	display: (target?: string) => Promise<unknown>;
	destroy: () => void;
	prev: () => void;
	next: () => void;
	book?: { package?: { metadata?: { direction?: string } } };
};

type EpubBookRuntime = {
	renderTo: (
		element: HTMLElement,
		options: { width: number; height: number; spread: 'always' | 'none' | 'auto'; flow: 'paginated' },
	) => EpubRenditionRuntime;
	ready: Promise<unknown>;
	locations: {
		generate: (chars: number) => Promise<unknown>;
		locationFromCfi: (cfi: string) => number;
		total: number;
	};
	navigation: { toc: TocItem[] };
	package: { metadata: { direction?: string } };
	destroy: () => void;
};

const EPUB_LOCATION_BREAK_SIZE = 1600;

function getInitialSpread(): 'always' | 'none' {
	return typeof window !== 'undefined' && window.innerWidth < 768 ? 'none' : 'always';
}

function EpubReader({ bookId }: { bookId: number }) {
	const mainRef = useRef<HTMLElement | null>(null);
	const viewerRef = useRef<HTMLDivElement | null>(null);
	const currentCfiRef = useRef('');
	const renditionRef = useRef<EpubRenditionRuntime | null>(null);
	const [spread, setSpread] = useState<'always' | 'none' | 'auto'>(getInitialSpread);
	const [percentage, setPercentage] = useState(0);
	const [totalPage, setTotalPage] = useState(0);
	const [currentPage, setCurrentPage] = useState(0);
	const [isLoading, setIsLoading] = useState(true);
	const [fileError, setFileError] = useState(false);
	const [epubData, setEpubData] = useState<ArrayBuffer | null>(null);
	const [readingInfoReady, setReadingInfoReady] = useState(false);
	const [navigation, setNavigation] = useState<{ toc: TocItem[] } | null>(null);
	const [history, setHistory] = useState<string[]>([]);
	const storageKey = `reader-location:${bookId}`;

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
				if (readingInfo.currentPosition?.startsWith('epubcfi(')) {
					localStorage.setItem(storageKey, readingInfo.currentPosition);
				}
			})
			.catch(() => undefined)
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

		// epub.js opens ZIP archives from binary input. A blob: URL is treated as an
		// unpacked publication URL and leaves book.ready pending for uploaded EPUBs.
		const book = ePub(epubData) as unknown as EpubBookRuntime;
		const rendition = book.renderTo(viewerRef.current, {
			width: viewerRef.current.clientWidth,
			height: viewerRef.current.clientHeight,
			spread,
			flow: 'paginated',
		});
		renditionRef.current = rendition;
		let disposed = false;

		const handleRelocated = (location: RelocatedLocation) => {
			const cfi = location.start.cfi;
			currentCfiRef.current = cfi;

			const page = book.locations.locationFromCfi(cfi);
			const total = book.locations.total;
			// epub.js exposes total as the final zero-based location index, not a count.
			const lastLocation = Math.max(0, total);
			const progress = total <= 1 ? 100 : Math.round((page / lastLocation) * 100);
			const readStatus = total > 0 && page >= lastLocation ? 'completed' : 'reading';

			setPercentage(Math.max(0, Math.min(100, progress)));
			setTotalPage(total);
			setCurrentPage(page);
			localStorage.setItem(storageKey, cfi);
			void saveReadingInfo(bookId, cfi, readStatus).catch(() => undefined);
		};

		const handleKeyUp = (event: KeyboardEvent) => {
			if (event.key === 'ArrowRight') {
				if (book.package.metadata.direction === 'rtl') {
					rendition.prev();
				} else {
					rendition.next();
				}
			}

			if (event.key === 'ArrowLeft') {
				if (book.package.metadata.direction === 'rtl') {
					rendition.next();
				} else {
					rendition.prev();
				}
			}
		};

		void book.ready
			.then(async () => {
				await book.locations.generate(EPUB_LOCATION_BREAK_SIZE);
				if (disposed) return;

				setNavigation(book.navigation);
				rendition.on('relocated', handleRelocated);
				rendition.on('keyup', handleKeyUp);
				document.addEventListener('keyup', handleKeyUp);

				const savedCfi = localStorage.getItem(storageKey);
				await rendition.display(savedCfi ?? undefined);
				if (!disposed) {
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
			rendition.off?.('relocated', handleRelocated);
			rendition.off?.('keyup', handleKeyUp);
			document.removeEventListener('keyup', handleKeyUp);
			rendition.destroy();
			book.destroy();
			if (renditionRef.current === rendition) {
				renditionRef.current = null;
			}
		};
	}, [bookId, epubData, readingInfoReady, spread, storageKey]);

	const handlePrev = () => {
		const book = renditionRef.current?.book;

		if (book?.package?.metadata?.direction === 'rtl') {
			renditionRef.current?.next();
		} else {
			renditionRef.current?.prev();
		}
	};

	const handleNext = () => {
		const book = renditionRef.current?.book;

		if (book?.package?.metadata?.direction === 'rtl') {
			renditionRef.current?.prev();
		} else {
			renditionRef.current?.next();
		}
	};

	const handleTocJump = (href: string) => {
		if (currentCfiRef.current) {
			setHistory((prev) => [...prev, currentCfiRef.current].slice(-20));
		}
		void renditionRef.current?.display(href);
	};

	const handleBack = () => {
		const prev = history.at(-1);
		if (!prev) return;

		void renditionRef.current?.display(prev);
		setHistory((prevHistory) => prevHistory.slice(0, -1));
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
				<div className='flex items-center gap-2'>
					<Link to='/' className='inline-flex min-h-11 shrink-0 items-center rounded-md px-2 text-sm text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'>
						本棚に戻る
					</Link>
					<div className='ml-auto flex items-center gap-2'>
						{history.length > 0 && (
							<Button variant='outline' onClick={handleBack} className='min-h-11 px-3'>戻る</Button>
						)}
						<Sheet>
							<SheetTrigger asChild>
								<Button variant='outline' className='min-h-11 bg-white px-3 active:bg-white focus:bg-white data-[state=open]:bg-white'>
									目次
								</Button>
							</SheetTrigger>
							<SheetContent side='right' className='w-[calc(100vw-2rem)] max-w-sm data-[side=bottom]:max-h-[50vh] data-[side=top]:max-h-[50vh] [&>button]:size-11'>
							<SheetHeader>
								<SheetTitle>この書籍の目次</SheetTitle>
							</SheetHeader>
							<div className='no-scrollbar overflow-y-auto px-4'>
								{navigation?.toc.map((item, index) => (
									<div key={`${item.href}-${index}`}>
										<SheetTrigger asChild>
											<Button variant='outline' className='min-h-11 h-auto w-full whitespace-normal bg-white py-2 text-left' onClick={() => handleTocJump(item.href)}>
												{item.label}
											</Button>
										</SheetTrigger>
									</div>
								))}
							</div>
							</SheetContent>
						</Sheet>

						<Select value={spread} onValueChange={(value) => setSpread(value as 'always' | 'none' | 'auto')}>
							<SelectTrigger aria-label='ページ表示方法' className='min-h-11 w-[132px] bg-reader-background sm:w-[180px]'>
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
						読書位置: {currentPage} / {totalPage}
						<span className='ml-auto'>{percentage}%</span>
					</FieldLabel>
					<Progress value={percentage} id='reading-progress' />
				</Field>
			</header>

			<main ref={mainRef} tabIndex={0} className='relative min-h-0 flex-1 overflow-hidden bg-white px-1 outline-none sm:px-4'>
				{isLoading && (
					<div className='absolute inset-0 z-50 flex items-center justify-center bg-reader-background'>
						<Spinner className='size-16' />
					</div>
				)}
				<div ref={viewerRef} tabIndex={1} className='h-full w-full overflow-hidden rounded bg-card shadow' />
			</main>
			<footer className='flex shrink-0 justify-center gap-3 border-t bg-background p-2'>
				<Button type='button' variant='outline' size='icon' onClick={handlePrev} aria-label='前のページへ' className='size-11 rounded-full text-2xl'>‹</Button>
				<Button type='button' variant='outline' size='icon' onClick={handleNext} aria-label='次のページへ' className='size-11 rounded-full text-2xl'>›</Button>
			</footer>
		</div>
	);
}

export default EpubReader;
