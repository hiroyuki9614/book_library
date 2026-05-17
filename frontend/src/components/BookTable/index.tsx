import React from 'react';
import { useNavigate } from 'react-router-dom';
import { MoreHorizontal, Star } from 'lucide-react';

import { Table, TableBody, TableCell, TableFooter, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { booksData } from '@/data/booksData';

export function BookTable() {
	const itemsPerPage = 5;
	const totalItems = booksData.length;
	const totalPages = Math.ceil(totalItems / itemsPerPage);

	const [currentPage, setCurrentPage] = React.useState(1);

	const startIndex = (currentPage - 1) * itemsPerPage;
	const endIndex = startIndex + itemsPerPage;
	const currentBooks = booksData.slice(startIndex, endIndex);

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
					<TableHead />
					<TableHead>タイトル</TableHead>
					<TableHead>著者</TableHead>
					<TableHead>ジャンル</TableHead>
					<TableHead>ステータス</TableHead>
					<TableHead>進捗</TableHead>
					<TableHead />
				</TableRow>
			</TableHeader>

			<TableBody>
				{currentBooks.map((book) => (
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
							<Progress value={book.progress} className='w-24' />
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
				))}
			</TableBody>

			<TableFooter>
				<TableRow>
					<TableCell colSpan={7} className='text-center'>
						<div className='flex items-center justify-center gap-4 py-4'>
							<button className='rounded bg-primary text-white px-3 py-1 disabled:opacity-50' disabled={currentPage === 1} onClick={handlePreviousPage}>
								前へ
							</button>

							<span>
								{currentPage} / {totalPages}
							</span>

							<button className='rounded bg-primary text-white px-3 py-1 disabled:opacity-50' disabled={currentPage === totalPages} onClick={handleNextPage}>
								次へ
							</button>
						</div>
					</TableCell>
				</TableRow>
			</TableFooter>
		</Table>
	);
}
