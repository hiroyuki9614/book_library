import { useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import ePub from 'epubjs';

function ReaderPageT() {
	const viewerRef = useRef<HTMLDivElement | null>(null);
	const bookRef = useRef<any>(null);
	const renditionRef = useRef<any>(null);

	useEffect(() => {
		if (!viewerRef.current) return;

		const book = ePub('/shashinnonezumi.epub');
		bookRef.current = book;

		const rendition = book.renderTo(viewerRef.current, {
			width: viewerRef.current.clientWidth,
			height: viewerRef.current.clientHeight,
			spread: 'none',
		});

		renditionRef.current = rendition;

		const savedLocation = localStorage.getItem('reader-location');

		rendition.display(savedLocation || undefined);

		rendition.on('relocated', (location: any) => {
			localStorage.setItem('reader-location', location.start.cfi);
		});

		return () => {
			rendition.destroy();
			book.destroy();
		};
	}, []);

	return (
		<div className='flex h-screen flex-col bg-background text-foreground'>
			<header className='flex items-center justify-between border-b px-4 py-3'>
				<div>
					<h1 className='text-lg font-bold'>Reader Page</h1>

					<Link to='/' className='text-sm text-primary'>
						本棚に戻る
					</Link>
				</div>

				<div className='flex gap-2'>
					<button onClick={() => renditionRef.current?.prev()} className='rounded border px-3 py-1 text-sm'>
						前へ
					</button>

					<button onClick={() => renditionRef.current?.next()} className='rounded border px-3 py-1 text-sm'>
						次へ
					</button>
				</div>
			</header>

			<main className='relative h-[calc(100vh-73px)] overflow-hidden p-4'>
				<div ref={viewerRef} className='h-full w-full overflow-hidden rounded bg-card shadow' />

				<button onClick={() => renditionRef.current?.prev()} className='absolute left-8 top-1/2 z-10 -translate-y-1/2 rounded-full bg-background/80 p-3 shadow backdrop-blur'>
					←
				</button>

				<button onClick={() => renditionRef.current?.next()} className='absolute right-8 top-1/2 z-10 -translate-y-1/2 rounded-full bg-background/80 p-3 shadow backdrop-blur'>
					→
				</button>
			</main>
		</div>
	);
}

export default ReaderPageT;
