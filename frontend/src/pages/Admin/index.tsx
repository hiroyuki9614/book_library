import { useState } from 'react';
import { BookOpen, Library, Plus } from 'lucide-react';
import { toast } from 'sonner';

import BookRegistar, { type BookRegistrationValues } from '@/components/forms/BookRegistar';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from '@/components/ui/sheet';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { booksData } from '@/data/booksData';

type AdminBook = {
	id: number;
	title: string;
	authorName: string;
	categoryName: string;
	publishedAt: string;
	pageTurnDirection: 'ltr' | 'rtl';
	fileName?: string;
};

const initialBooks: AdminBook[] = booksData.slice(0, 5).map((book) => ({
	id: book.id,
	title: book.title,
	authorName: book.author,
	categoryName: book.category,
	publishedAt: book.year?.toString() ?? '—',
	pageTurnDirection: 'ltr',
}));

function Admin() {
	const [books, setBooks] = useState(initialBooks);
	const [isSheetOpen, setIsSheetOpen] = useState(false);

	const handleBookRegistration = (values: BookRegistrationValues) => {
		setBooks((currentBooks) => [
			{
				id: Math.max(0, ...currentBooks.map((book) => book.id)) + 1,
				title: values.title,
				authorName: values.authorName || '未設定',
				categoryName: values.categoryName,
				publishedAt: values.publishedAt || '—',
				pageTurnDirection: values.pageTurnDirection,
				fileName: values.file?.[0]?.name,
			},
			...currentBooks,
		]);
		setIsSheetOpen(false);
		toast.success('書籍を登録しました。');
	};

	return (
		<main className='mx-auto w-full max-w-7xl space-y-6 p-4 md:p-8'>
			<header className='flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between'>
				<div>
					<p className='mb-1 text-sm font-medium text-muted-foreground'>ADMIN CONSOLE</p>
					<h1 className='text-3xl font-semibold tracking-tight'>書籍管理</h1>
					<p className='mt-2 text-sm text-muted-foreground'>書籍情報とファイルを登録・管理します。</p>
				</div>

				<Sheet open={isSheetOpen} onOpenChange={setIsSheetOpen}>
					<SheetTrigger asChild>
						<Button size='lg'>
							<Plus />
							新しい書籍を登録
						</Button>
					</SheetTrigger>
					<SheetContent className='w-full sm:max-w-xl'>
						<SheetHeader className='border-b px-5 py-5'>
							<SheetTitle className='text-xl'>書籍を登録</SheetTitle>
							<SheetDescription>基本情報を入力してください。タイトルのみ必須です。</SheetDescription>
						</SheetHeader>
						<BookRegistar onSubmit={handleBookRegistration} />
					</SheetContent>
				</Sheet>
			</header>

			<section className='grid gap-4 sm:grid-cols-2'>
				<Card>
					<CardHeader className='flex-row items-center justify-between'>
						<div>
							<CardDescription>登録書籍</CardDescription>
							<CardTitle className='mt-2 text-3xl'>{books.length}</CardTitle>
						</div>
						<Library className='size-7 text-muted-foreground' />
					</CardHeader>
				</Card>
				<Card>
					<CardHeader className='flex-row items-center justify-between'>
						<div>
							<CardDescription>ファイル登録済み</CardDescription>
							<CardTitle className='mt-2 text-3xl'>{books.filter((book) => book.fileName).length}</CardTitle>
						</div>
						<BookOpen className='size-7 text-muted-foreground' />
					</CardHeader>
				</Card>
			</section>

			<Card>
				<CardHeader>
					<CardTitle>書籍一覧</CardTitle>
					<CardDescription>登録されている書籍を確認できます。</CardDescription>
				</CardHeader>
				<CardContent className='overflow-x-auto'>
					<Table className='min-w-[720px]'>
						<TableHeader>
							<TableRow>
								<TableHead>タイトル</TableHead>
								<TableHead>著者</TableHead>
								<TableHead>カテゴリ</TableHead>
								<TableHead>出版日</TableHead>
								<TableHead>ページ方向</TableHead>
								<TableHead>ファイル</TableHead>
							</TableRow>
						</TableHeader>
						<TableBody>
							{books.map((book) => (
								<TableRow key={book.id}>
									<TableCell className='font-medium'>{book.title}</TableCell>
									<TableCell>{book.authorName}</TableCell>
									<TableCell>{book.categoryName}</TableCell>
									<TableCell>{book.publishedAt}</TableCell>
									<TableCell>{book.pageTurnDirection === 'rtl' ? '右から左' : '左から右'}</TableCell>
									<TableCell>
										{book.fileName ? <Badge variant='secondary'>{book.fileName}</Badge> : <span className='text-muted-foreground'>未登録</span>}
									</TableCell>
								</TableRow>
							))}
						</TableBody>
					</Table>
				</CardContent>
			</Card>
		</main>
	);
}

export default Admin;
