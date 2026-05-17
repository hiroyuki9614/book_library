import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import ePub from 'epubjs';
import { Field, FieldLabel } from '@/components/ui/field';
import { Progress } from '@/components/ui/progress';
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectSeparator, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Spinner } from '@/components/ui/spinner';
import { Button } from '@/components/ui/button';
import { Sheet, SheetClose, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle, SheetTrigger } from '@/components/ui/sheetCapture';

function ReaderPageT() {
	const mainRef = useRef<HTMLElement | null>(null);
	const viewerRef = useRef<HTMLDivElement | null>(null);
	const currentCfiRef = useRef('');
	const renditionRef = useRef<any>(null);
	const [spread, setSpread] = useState<'always' | 'none' | 'auto'>('always');
	const [percentage, setPercentage] = useState<string>('0');
	const [totalPage, setTotalPage] = useState<number>(0);
	const [currentPage, setCurrentPage] = useState<number>(0);
	const [cfi, setCfi] = useState<string>('');
	const [isLoading, setIsLoading] = useState(true);
	const [navigation, setNavigation] = useState<any>(null);
	const [history, setHistory] = useState<string[]>([]);

	useEffect(() => {
		if (!viewerRef.current) return;
		setIsLoading(true);

		viewerRef.current.innerHTML = '';

		const book = ePub('/見てしまう人びと 幻覚の脳科学.epub');

		const rendition = book.renderTo(viewerRef.current, {
			width: viewerRef.current.clientWidth,
			height: viewerRef.current.clientHeight,
			spread,
			flow: 'paginated',
		});

		renditionRef.current = rendition;

		const handleRelocated = (location: any) => {
			const cfi = location.start.cfi;

			const currentPage = book.locations.locationFromCfi(cfi);
			const totalPage = book.locations.total;

			setNavigation(book.navigation);

			const percentage = totalPage > 0 ? Math.round((currentPage / totalPage) * 100) : 0;

			setPercentage(percentage);
			setTotalPage(totalPage);
			setCurrentPage(currentPage);
			// ***************************************************************
			// 暫定 DBから読み込むようにするまでは localStorage に保存しておく
			// ***************************************************************
			localStorage.setItem('reader-location', cfi);
			setCfi(cfi);
		};

		rendition.on('relocated', (location) => {
			currentCfiRef.current = location.start.cfi;
		});

		const handleTocJump = (href: string) => {
			setHistory((prev) => [...prev, currentCfiRef.current].slice(-20));

			renditionRef.current?.display(href);
		};

		const handleKeyUp = (e: KeyboardEvent) => {
			if (e.key === 'ArrowRight') {
				if (book.package.metadata.direction === 'rtl') {
					rendition.prev();
				} else {
					rendition.next();
				}
			}

			if (e.key === 'ArrowLeft') {
				if (book.package.metadata.direction === 'rtl') {
					rendition.next();
				} else {
					rendition.prev();
				}
			}
		};

		book.ready.then(async () => {
			await book.locations.generate(100);

			rendition.on('relocated', handleRelocated);
			rendition.on('keyup', handleKeyUp);
			document.addEventListener('keyup', handleKeyUp);
			// ***************************************************************
			// 暫定 DBから読み込むようにするまでは localStorage に保存しておく
			// ***************************************************************
			const savedCfi = localStorage.getItem('reader-location');

			if (savedCfi) {
				await rendition.display(savedCfi);
			} else {
				await rendition.display();
			}
			setIsLoading(false);
		});

		return () => {
			rendition.off?.('relocated', handleRelocated);
			rendition.off?.('keyup', handleKeyUp);
			document.removeEventListener('keyup', handleKeyUp);

			rendition.destroy();
			book.destroy();
		};
	}, [spread]);

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

	// ユーザー操作時に戻る
	const handleTocJump = (href: string) => {
		if (currentCfiRef.current) {
			setHistory((prev) => [...prev, currentCfiRef.current].slice(-20));
		}

		renditionRef.current?.display(href);
	};
	const handleBack = () => {
		const prev = history.at(-1);

		if (!prev) return;

		renditionRef.current?.display(prev);

		setHistory((prevHistory) => prevHistory.slice(0, -1));
	};

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
						<Progress value={Number(percentage)} id='progress-upload' />
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
									<div key={index} className='ml-[calc(var(--level)*16px)]'>
										<SheetTrigger asChild>
											<Button
												variant='outline'
												className='bg-white'
												onClick={() => {
													handleTocJump(item.href);
												}}
											>
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

export default ReaderPageT;
