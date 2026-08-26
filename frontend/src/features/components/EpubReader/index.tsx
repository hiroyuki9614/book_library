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

type EpubRenditionRuntime = {
	on: (event: string, handler: (value: any) => void) => void;
	off?: (event: string, handler: (value: any) => void) => void;
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

function EpubReader({ bookId }: { bookId: number }) {
	const mainRef = useRef<HTMLElement | null>(null);
	const viewerRef = useRef<HTMLDivElement | null>(null);
	const currentCfiRef = useRef('');
	const renditionRef = useRef<EpubRenditionRuntime | null>(null);
	const [spread, setSpread] = useState<'always' | 'none' | 'auto'>('always');
	const [percentage, setPercentage] = useState(0);
	const [totalPage, setTotalPage] = useState(0);
	const [currentPage, setCurrentPage] = useState(0);
	const [isLoading, setIsLoading] = useState(true);
	const [fileError, setFileError] = useState(false);
	const [epubUrl, setEpubUrl] = useState<string | null>(null);
	const [readingInfoReady, setReadingInfoReady] = useState(false);
	const [navigation, setNavigation] = useState<{ toc: TocItem[] } | null>(null);
	const [history, setHistory] = useState<string[]>([]);
	const storageKey = `reader-location:${bookId}`;

	useEffect(() => {
		let objectUrl: string | undefined;
		let active = true;

		setEpubUrl(null);
		setFileError(false);
		setIsLoading(true);

		void fetchBookFile(bookId)
			.then((blob) => {
				if (!active) return;
				objectUrl = URL.createObjectURL(blob);
				setEpubUrl(objectUrl);
			})
			.catch(() => {
				if (!active) return;
				setFileError(true);
				setIsLoading(false);
			});

		return () => {
			active = false;
			if (objectUrl) {
				URL.revokeObjectURL(objectUrl);
			}
		};
	}, [bookId]);

	useEffect(() => {
		let active = true;
		setReadingInfoReady(false);

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
		if (!viewerRef.current || !epubUrl || !readingInfoReady) return;
		setIsLoading(true);
		setFileError(false);
		viewerRef.current.innerHTML = '';

		const book = ePub(epubUrl) as unknown as EpubBookRuntime;
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
			const lastLocation = Math.max(0, total - 1);
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
				await book.locations.generate(100);
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
	}, [bookId, epubUrl, readingInfoReady, spread, storageKey]);

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
		<div className='flex h-screen flex-col bg-background text-foreground'>
			<header className='flex items-center justify-between p-3'>
				<div>
					<Link to='/' className='text-sm text-primary'>
						本棚に戻る
					</Link>
				</div>
				<div className='flex flex-row items-end gap-1'>
					<Field className='w-full max-w-sm'>
						<FieldLabel htmlFor='progress-upload'>
							読書位置: {currentPage} / {totalPage}
							<span className='ml-auto'>{percentage}%</span>
						</FieldLabel>
						<Progress value={percentage} id='progress-upload' />
					</Field>
				</div>
				<div>
					{history.length > 0 && (
						<Button variant='outline' onClick={handleBack}>
							戻る
						</Button>
					)}
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
							<div className='no-scrollbar overflow-y-auto px-4'>
								{navigation?.toc.map((item, index) => (
									<div key={`${item.href}-${index}`}>
										<SheetTrigger asChild>
											<Button variant='outline' className='bg-white' onClick={() => handleTocJump(item.href)}>
												{item.label}
											</Button>
										</SheetTrigger>
									</div>
								))}
							</div>
						</SheetContent>
					</Sheet>
				</div>

				<Select value={spread} onValueChange={(value) => setSpread(value as 'always' | 'none' | 'auto')}>
					<SelectTrigger className='w-[180px] bg-reader-background'>
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
			</header>

			<main ref={mainRef} tabIndex={0} className='relative h-[calc(100vh-73px)] overflow-hidden outline-none bg-white px-4'>
				{isLoading && (
					<div className='absolute inset-0 z-50 flex items-center justify-center bg-reader-background'>
						<Spinner className='size-16' />
					</div>
				)}
				<div ref={viewerRef} tabIndex={1} className='h-full w-full overflow-hidden rounded bg-card shadow' />
				<button type='button' onClick={handlePrev} aria-label='前のページへ' className='absolute left-0 top-1/2 z-10 h-full w-5 -translate-y-1/2 bg-background/0' />
				<button type='button' onClick={handleNext} aria-label='次のページへ' className='absolute right-0 top-1/2 z-10 h-full w-5 -translate-y-1/2 bg-background/0' />
			</main>
		</div>
	);
}

export default EpubReader;
