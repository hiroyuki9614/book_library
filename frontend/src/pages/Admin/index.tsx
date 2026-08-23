import { useEffect, useState } from 'react';
import { BookOpen, Library, Plus } from 'lucide-react';
import { toast } from 'sonner';

import { fetchAdminBooks, fetchAdminCategories, registerAdminBook, type AdminBook, type AdminCategory } from '@/api/admin';
import BookRegistar, { type BookRegistrationValues } from '@/components/forms/BookRegistar';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from '@/components/ui/sheet';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';

function formatPublishedAt(value: string | null) {
	return value ? value.slice(0, 10) : '—';
}

function formatFileSize(bytes: number) {
	if (bytes < 1024 * 1024) {
		return `${Math.max(1, Math.round(bytes / 1024))} KB`;
	}
	return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function Admin() {
	const [books, setBooks] = useState<AdminBook[]>([]);
	const [booksLoading, setBooksLoading] = useState(true);
	const [booksError, setBooksError] = useState<string | null>(null);
	const [categories, setCategories] = useState<AdminCategory[]>([]);
	const [categoriesLoading, setCategoriesLoading] = useState(true);
	const [categoriesError, setCategoriesError] = useState<string | null>(null);
	const [isSheetOpen, setIsSheetOpen] = useState(false);

	useEffect(() => {
		let isMounted = true;
		setCategoriesLoading(true);
		setBooksLoading(true);

		fetchAdminCategories()
			.then((loadedCategories) => {
				if (!isMounted) return;
				setCategories(loadedCategories);
				setCategoriesError(null);
			})
			.catch(() => {
				if (!isMounted) return;
				setCategoriesError('カテゴリの取得に失敗しました。再読み込みしてください。');
			})
			.finally(() => {
				if (isMounted) setCategoriesLoading(false);
			});

		fetchAdminBooks()
			.then((loadedBooks) => {
				if (!isMounted) return;
				setBooks(loadedBooks);
				setBooksError(null);
			})
			.catch(() => {
				if (!isMounted) return;
				setBooksError('登録済み書籍の取得に失敗しました。再読み込みしてください。');
			})
			.finally(() => {
				if (isMounted) setBooksLoading(false);
			});

		return () => {
			isMounted = false;
		};
	}, []);

	const handleBookRegistration = async (values: BookRegistrationValues) => {
		const file = values.file[0];
		if (!file) {
			throw new Error('Book file is required');
		}

		const registeredBook = await registerAdminBook({
			title: values.title,
			authorName: values.authorName,
			publisher: values.publisher,
			publishedAt: values.publishedAt,
			categoryId: values.categoryId,
			pageTurnDirection: values.pageTurnDirection,
			description: values.description,
			publicationScope: values.publicationScope,
			file,
		});
		setBooks((currentBooks) => [registeredBook, ...currentBooks.filter((book) => book.id !== registeredBook.id)]);
		setIsSheetOpen(false);
		toast.success('書籍とファイルを登録しました。');
	};

	const fileCount = books.filter((book) => book.file !== null).length;

	return (
		<main className='mx-auto w-full max-w-7xl space-y-6 p-4 md:p-8'>
			<header className='flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between'>
				<div>
					<p className='mb-1 text-sm font-medium text-muted-foreground'>ADMIN CONSOLE</p>
					<h1 className='text-3xl font-semibold tracking-tight'>書籍管理</h1>
					<p className='mt-2 text-sm text-muted-foreground'>EPUB/PDFと書籍情報を実データとして登録・確認します。</p>
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
							<SheetDescription>タイトル・カテゴリ・公開範囲・EPUB/PDFファイルが必須です。</SheetDescription>
						</SheetHeader>
						<BookRegistar
							categories={categories}
							categoriesLoading={categoriesLoading}
							categoriesError={categoriesError}
							onSubmit={handleBookRegistration}
						/>
					</SheetContent>
				</Sheet>
			</header>

			<section className='grid gap-4 sm:grid-cols-2'>
				<Card>
					<CardHeader className='flex-row items-center justify-between'>
						<div>
							<CardDescription>登録済み書籍</CardDescription>
							<CardTitle className='mt-2 text-3xl'>{books.length}</CardTitle>
						</div>
						<Library className='size-7 text-muted-foreground' />
					</CardHeader>
				</Card>
				<Card>
					<CardHeader className='flex-row items-center justify-between'>
						<div>
							<CardDescription>ファイル登録済み</CardDescription>
							<CardTitle className='mt-2 text-3xl'>{fileCount}</CardTitle>
						</div>
						<BookOpen className='size-7 text-muted-foreground' />
					</CardHeader>
				</Card>
			</section>

			<Card>
				<CardHeader>
					<CardTitle>登録済み書籍</CardTitle>
					<CardDescription>PostgreSQLに保存されている書籍を再読み込み後も表示します。</CardDescription>
				</CardHeader>
				<CardContent className='overflow-x-auto'>
					{booksLoading && <p className='mb-4 text-sm text-muted-foreground'>登録済み書籍を読み込んでいます…</p>}
					{booksError && <p role='alert' className='mb-4 text-sm text-destructive'>{booksError}</p>}
					<Table className='min-w-[900px]'>
						<TableHeader>
							<TableRow>
								<TableHead>タイトル</TableHead>
								<TableHead>著者</TableHead>
								<TableHead>カテゴリ</TableHead>
								<TableHead>出版日</TableHead>
								<TableHead>公開範囲</TableHead>
								<TableHead>ファイル</TableHead>
							</TableRow>
						</TableHeader>
						<TableBody>
							{books.map((book) => (
								<TableRow key={book.id}>
									<TableCell className='font-medium'>{book.title}</TableCell>
									<TableCell>{book.authorName ?? '未設定'}</TableCell>
									<TableCell>{book.category.name}</TableCell>
									<TableCell>{formatPublishedAt(book.publishedAt)}</TableCell>
									<TableCell>{book.publicationScope === 'all_users' ? '全ユーザー公開' : '管理者のみ'}</TableCell>
									<TableCell>
										{book.file ? (
											<div className='flex items-center gap-2'>
												<Badge variant='secondary'>{book.file.extension.toUpperCase()}</Badge>
												<span>{book.file.originalFileName}</span>
												<span className='text-xs text-muted-foreground'>{formatFileSize(book.file.fileSize)}</span>
											</div>
										) : (
											<Badge variant='outline'>未登録</Badge>
										)}
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
