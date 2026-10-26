import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { BookOpen, MoreHorizontal, Star } from 'lucide-react';

import { Table, TableBody, TableCell, TableFooter, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import type { Book } from '@/data/booksData';
import { Skeleton } from '@/components/ui/skeleton';
import type { ReadingProgress } from '@/data/readingProgress';

type StatusFilter = 'all' | 'unread' | 'reading' | 'completed';

type BookTableProps = {
	books: Book[];
	readingProgresses?: ReadingProgress[];
	itemsPerPage: number;
	searchQuery: string;
	sort: string;
	isLoading: boolean;
	status: StatusFilter;
};

export function BookTable({ books, readingProgresses = [], itemsPerPage, searchQuery, sort, isLoading, status }: BookTableProps) {
	const columns = ['', 'タイトル', '著者', 'ジャンル', 'ステータス', '進捗'];
	const [currentPage, setCurrentPage] = useState<number>(1);

	const getReadingProgress = (bookId: number) => readingProgresses.find((progress) => progress.id === bookId);
	const getBookStatus = (book: Book): Exclude<StatusFilter, 'all'> => book.status.toLowerCase() as Exclude<StatusFilter, 'all'>;

	const filteredBooks = books.filter((book) => {
		const query = searchQuery.toLowerCase();
		const matchesSearch = book.title.toLowerCase().includes(query) || book.author.toLowerCase().includes(query);
		const matchesStatus = status === 'all' || getBookStatus(book) === status;
		return matchesSearch && matchesStatus;
	});

	const totalItems = filteredBooks.length;
	const totalPages = Math.max(1, Math.ceil(totalItems / itemsPerPage));
	const safeCurrentPage = Math.min(currentPage, totalPages);
	const startIndex = (safeCurrentPage - 1) * itemsPerPage;
	const endIndex = startIndex + itemsPerPage;

	const sortedBooks = [...filteredBooks].sort((a, b) => {
		if (sort === 'newest') return b.id - a.id;
		return a.id - b.id;
	});
	const currentBooks = sortedBooks.slice(startIndex, endIndex);
	const navigate = useNavigate();

	const getBadgeVariant = (value: string) => {
		switch (value.toLowerCase()) {
			case 'reading':
				return 'default';
			case 'completed':
				return 'secondary';
			case 'on hold':
				return 'destructive';
			case 'dropped':
				return 'outline';
			case 'unread':
				return 'ghost';
			default:
				return 'default';
		}
	};

	function handlePreviousPage() {
		if (currentPage > 1) setCurrentPage((prevPage) => prevPage - 1);
	}

	function handleNextPage() {
		if (currentPage < totalPages) setCurrentPage((prevPage) => prevPage + 1);
	}

	return (
		<Table className='block w-full md:table md:min-w-[720px] md:table-fixed'>
			<TableHeader className='hidden md:table-header-group'>
				<TableRow>
					{columns.map((column, index) => <TableHead key={index}>{column}</TableHead>)}
					<TableHead />
				</TableRow>
			</TableHeader>

			<TableBody className='block space-y-3 md:table-row-group md:space-y-0'>
				{isLoading
					? [...Array(itemsPerPage)].map((_, index) => (
							<TableRow key={index} className='grid grid-cols-[64px_minmax(0,1fr)] rounded-lg border bg-card p-2 md:table-row md:rounded-none md:p-0'>
								{[...Array(columns.length + 1)].map((_, cellIndex) => (
									<TableCell key={cellIndex}><Skeleton className='h-16 w-full' /></TableCell>
								))}
							</TableRow>
						))
					: currentBooks.map((book) => {
							const progress = getReadingProgress(book.id)?.progress;
							const bookStatus = getBookStatus(book);

							return (
								<TableRow
									key={book.id}
									role='link'
									tabIndex={0}
									aria-label={`${book.title}を読む`}
									onClick={() => navigate(`/reader/${book.id}`)}
									onKeyDown={(event) => {
										if (event.key === 'Enter' || event.key === ' ') {
											event.preventDefault();
											navigate(`/reader/${book.id}`);
										}
									}}
									className='grid min-h-24 cursor-pointer grid-cols-[64px_minmax(0,1fr)] rounded-lg border bg-card p-2 shadow-sm hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring md:table-row md:rounded-none md:border-x-0 md:p-0 md:shadow-none'
								>
									<TableCell className='row-span-3 p-2 md:table-cell'>
										{book.coverImage ? (
											<img src={book.coverImage} alt={`${book.title} の表紙`} className='h-16 w-12 rounded object-cover' />
										) : (
											<div aria-label={`${book.title} の表紙未設定`} className='flex h-16 w-12 flex-col items-center justify-center rounded bg-primary/10 text-primary'>
												<BookOpen className='h-5 w-5' />
												<span className='mt-1 text-[10px] font-semibold uppercase'>{book.fileType}</span>
											</div>
										)}
									</TableCell>
									<TableCell className='block min-w-0 overflow-hidden text-ellipsis whitespace-nowrap px-2 pb-1 pt-3 text-base font-semibold md:table-cell md:p-2 md:text-sm md:font-normal'>{book.title}</TableCell>
									<TableCell className='block min-w-0 overflow-hidden text-ellipsis whitespace-nowrap px-2 py-1 text-sm text-muted-foreground md:table-cell md:p-2 md:text-foreground'>{book.author}</TableCell>
									<TableCell className='hidden overflow-hidden text-ellipsis whitespace-nowrap md:table-cell'>{book.category}</TableCell>
									<TableCell className='block px-2 pb-3 pt-1 md:table-cell md:p-2'><Badge variant={getBadgeVariant(bookStatus)}>{bookStatus}</Badge></TableCell>
									<TableCell className='hidden md:table-cell'>{progress === undefined ? <span className='text-muted-foreground'>未計測</span> : <Progress value={progress} className='w-24' />}</TableCell>
									<TableCell className='hidden md:table-cell'>
										<div className='flex items-center gap-2'>
											<button
												aria-label={`${book.title} のメニューを開く`}
												onClick={(event) => event.stopPropagation()}
											>
												<MoreHorizontal className='h-4 w-4' />
											</button>
											<button
												aria-label={`${book.title} をお気に入りにする`}
												onClick={(event) => event.stopPropagation()}
											>
												<Star className='h-5 w-5' />
											</button>
										</div>
									</TableCell>
								</TableRow>
							);
						})}
				{currentBooks.length === 0 && !isLoading && (
					<TableRow>
						<TableCell colSpan={columns.length + 1} className='text-center py-4'>データがありません。</TableCell>
					</TableRow>
				)}
			</TableBody>

			<TableFooter className='block md:table-footer-group'>
				<TableRow className='block border-0 bg-card hover:bg-card md:table-row'>
					<TableCell colSpan={columns.length + 1} className='block text-center md:table-cell'>
						<div className='flex items-center justify-center gap-4 py-4'>
							<button className='min-h-11 min-w-16 cursor-pointer rounded bg-primary px-3 py-2 text-white disabled:opacity-50' disabled={currentPage === 1} onClick={handlePreviousPage}>前へ</button>
							<span>{currentPage} / {totalPages}</span>
							<button className='min-h-11 min-w-16 cursor-pointer rounded bg-primary px-3 py-2 text-white disabled:opacity-50' disabled={currentPage === totalPages} onClick={handleNextPage}>次へ</button>
						</div>
					</TableCell>
				</TableRow>
			</TableFooter>
		</Table>
	);
}
