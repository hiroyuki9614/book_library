import { useEffect, useState } from 'react';
import { BookOpen, Library, Plus, RotateCcw, Trash2 } from 'lucide-react';
import { toast } from 'sonner';

import {
	fetchAdminBooks,
	fetchAdminCategories,
	registerAdminBook,
	restoreAdminBook,
	softDeleteAdminBook,
	type AdminBook,
	type AdminBookState,
	type AdminCategory,
} from '@/api/admin';
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
	const [bookState, setBookState] = useState<AdminBookState>('active');
	const [booksLoading, setBooksLoading] = useState(true);
	const [booksError, setBooksError] = useState<string | null>(null);
	const [categories, setCategories] = useState<AdminCategory[]>([]);
	const [categoriesLoading, setCategoriesLoading] = useState(true);
	const [categoriesError, setCategoriesError] = useState<string | null>(null);
	const [isSheetOpen, setIsSheetOpen] = useState(false);
	const [mutatingBookId, setMutatingBookId] = useState<number | null>(null);

	useEffect(() => {
		let isMounted = true;
		setCategoriesLoading(true);

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

		return () => {
			isMounted = false;
		};
	}, []);

	useEffect(() => {
		let isMounted = true;
		setBooksLoading(true);
		setBooksError(null);

		fetchAdminBooks(bookState)
			.then((loadedBooks) => {
				if (!isMounted) return;
				setBooks(loadedBooks);
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
	}, [bookState]);

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
		setBookState('active');
		setBooks((currentBooks) => [{ ...registeredBook, deletedAt: null }, ...currentBooks.filter((book) => book.id !== registeredBook.id)]);
		setIsSheetOpen(false);
		toast.success('書籍とファイルを登録しました。');
	};

	const handleDelete = async (bookId: number) => {
		setMutatingBookId(bookId);
		try {
			await softDeleteAdminBook(bookId);
			setBooks((currentBooks) => currentBooks.filter((book) => book.id !== bookId));
			toast.success('書籍を削除済み一覧へ移動しました。');
		} catch {
			toast.error('書籍の論理削除に失敗しました。');
		} finally {
			setMutatingBookId(null);
		}
	};

	const handleRestore = async (bookId: number) => {
		setMutatingBookId(bookId);
		try {
			await restoreAdminBook(bookId);
			setBooks((currentBooks) => currentBooks.filter((book) => book.id !== bookId));
			toast.success('書籍を復元しました。');
		} catch {
			toast.error('書籍の復元に失敗しました。');
		} finally {
			setMutatingBookId(null);
		}
	};

	const fileCount = books.filter((book) => book.file !== null).length;
	const isDeletedView = bookState === 'deleted';

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
							<CardDescription>{isDeletedView ? '削除済み書籍' : '登録済み書籍'}</CardDescription>
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
				<CardHeader className='gap-4 sm:flex-row sm:items-center sm:justify-between'>
					<div>
						<CardTitle>{isDeletedView ? '削除済み書籍' : '登録済み書籍'}</CardTitle>
						<CardDescription>
							{isDeletedView ? '論理削除された書籍です。ファイルと読書記録は保持されています。' : 'PostgreSQLに保存されている通常書籍を表示します。'}
						</CardDescription>
					</div>
					<div className='flex gap-2'>
						<Button variant={bookState === 'active' ? 'default' : 'outline'} onClick={() => setBookState('active')}>通常書籍</Button>
						<Button variant={bookState === 'deleted' ? 'default' : 'outline'} onClick={() => setBookState('deleted')}>削除済み</Button>
					</div>
				</CardHeader>
				<CardContent className='overflow-x-auto'>
					{booksLoading && <p className='mb-4 text-sm text-muted-foreground'>書籍を読み込んでいます…</p>}
					{booksError && <p role='alert' className='mb-4 text-sm text-destructive'>{booksError}</p>}
					<Table className='min-w-[980px]'>
						<TableHeader>
							<TableRow>
								<TableHead>タイトル</TableHead>
								<TableHead>著者</TableHead>
								<TableHead>カテゴリ</TableHead>
								<TableHead>出版日</TableHead>
								<TableHead>公開範囲</TableHead>
								<TableHead>ファイル</TableHead>
								<TableHead className='w-28'>操作</TableHead>
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
									<TableCell>
										{isDeletedView ? (
											<Button size='sm' variant='outline' disabled={mutatingBookId === book.id} onClick={() => handleRestore(book.id)}>
												<RotateCcw />復元
											</Button>
										) : (
											<Button size='sm' variant='outline' disabled={mutatingBookId === book.id} onClick={() => handleDelete(book.id)}>
												<Trash2 />削除
											</Button>
										)}
									</TableCell>
								</TableRow>
							))}
							{!booksLoading && books.length === 0 && (
								<TableRow>
									<TableCell colSpan={7} className='py-8 text-center text-muted-foreground'>該当する書籍はありません。</TableCell>
								</TableRow>
							)}
						</TableBody>
					</Table>
				</CardContent>
			</Card>
		</main>
	);
}

export default Admin;
