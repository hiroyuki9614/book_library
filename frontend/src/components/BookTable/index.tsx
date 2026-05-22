import React from 'react';
import { useNavigate } from 'react-router-dom';
import { MoreHorizontal, Star } from 'lucide-react';

import { Table, TableBody, TableCell, TableFooter, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { booksData } from '@/data/booksData';
import { Skeleton } from '@/components/ui/skeleton';
import type { ReadingProgress } from '@/data/readingProgress';

type BookTableProps = {
	books: typeof booksData;
	readingProgresses: ReadingProgress[];
	itemsPerPage: number;
	searchQuery: string;
	sort: string;
	isLoading: boolean;
};

export function BookTable({ books, readingProgresses, itemsPerPage, searchQuery, sort, isLoading }: BookTableProps) {
	const columns = ['タイトル', '著者', 'ジャンル', 'ステータス', '進捗'];
	const [currentPage, setCurrentPage] = React.useState(1);

	const startIndex = (currentPage - 1) * itemsPerPage;
	const endIndex = startIndex + itemsPerPage;
	const filteredBooks = books.filter((book) => {
		const query = searchQuery.toLowerCase();
		return book.title.toLowerCase().includes(query) || book.author.toLowerCase().includes(query);
	});
	const totalItems = filteredBooks.length;
	const totalPages = Math.ceil(totalItems / itemsPerPage);
	const sortedBooks = [...filteredBooks].sort((a, b) => {
		if (sort === 'newest') {
			return b.id - a.id;
		}
		return a.id - b.id;
	});
	const currentBooks = sortedBooks.slice(startIndex, endIndex);

	const navigate = useNavigate();

	const getBadgeVariant = (status: string) => {
		switch (status.toLowerCase()) {
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
		if (currentPage > 1) {
			setCurrentPage((prevPage) => prevPage - 1);
		}
	}

	function handleNextPage() {
		if (currentPage < totalPages) {
			setCurrentPage((prevPage) => prevPage + 1);
		}
	}

	return (
		<Table>
			<TableHeader>
				<TableRow>
					{columns.map((column, index) => (
						<TableHead key={index}>{column}</TableHead>
					))}
					<TableHead />
				</TableRow>
			</TableHeader>

			<TableBody>
				{isLoading
					? [...Array(itemsPerPage)].map((_, index) => (
							<TableRow key={index}>
								{[...Array(columns.length + 1)].map((_, index) => (
									<TableCell key={index}>
										<Skeleton className='h-16 w-full' />
									</TableCell>
								))}
							</TableRow>
						))
					: currentBooks.map((book) => {
							const progress = readingProgresses.find((progress) => progress.id === book.id)?.progress || 0;
							return (
								<TableRow key={book.id} onClick={() => navigate(`/reader/${book.id}`)} className='cursor-pointer hover:bg-muted'>
									<TableCell>
										<img src={book.coverImage} alt={`${book.title} の表紙`} className='h-16 w-12 rounded object-cover' />
									</TableCell>

									<TableCell className='font-medium'>{book.title}</TableCell>

									<TableCell>{book.author}</TableCell>

									<TableCell>{book.category}</TableCell>

									<TableCell>
										<Badge variant={getBadgeVariant(book.status)}>{book.status}</Badge>
									</TableCell>

									<TableCell>
										<Progress value={progress} className='w-24' />
									</TableCell>

									<TableCell>
										<div className='flex items-center gap-2'>
											<button
												onClick={(event) => {
													event.stopPropagation();
												}}
											>
												<MoreHorizontal className='h-4 w-4' />
											</button>

											<button
												onClick={(event) => {
													event.stopPropagation();
												}}
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
						<TableCell colSpan={columns.length + 1} className='text-center py-4'>
							データがありません。
						</TableCell>
					</TableRow>
				)}
			</TableBody>

			<TableFooter>
				<TableRow className='bg-card hover:bg-card'>
					<TableCell colSpan={columns.length + 1} className='text-center'>
						<div className='flex items-center justify-center gap-4 py-4'>
							<button className='rounded bg-primary text-white px-3 py-2 disabled:opacity-50 cursor-pointer' disabled={currentPage === 1} onClick={handlePreviousPage}>
								前へ
							</button>

							<span>
								{currentPage} / {totalPages}
							</span>

							<button className='rounded bg-primary text-white px-3 py-2 disabled:opacity-50 cursor-pointer' disabled={currentPage === totalPages} onClick={handleNextPage}>
								次へ
							</button>
						</div>
					</TableCell>
				</TableRow>
			</TableFooter>
		</Table>
	);
}
