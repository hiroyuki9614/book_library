import { useEffect, useState } from 'react';
import { BookOpen, Library, Pencil, Plus, RotateCcw, Trash2 } from 'lucide-react';
import { toast } from 'sonner';

import {
	createAdminCategory,
	deleteAdminCategory,
	fetchAdminBooks,
	fetchAdminCategories,
	registerAdminBook,
	renameAdminCategory,
	restoreAdminBook,
	softDeleteAdminBook,
	updateAdminBookMetadata,
	type AdminBook,
	type AdminBookState,
	type AdminCategory,
} from '@/api/admin';
import BookEditor, { type BookEditorValues } from '@/components/forms/BookEditor';
import BookRegistar, { type BookRegistrationValues } from '@/components/forms/BookRegistar';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from '@/components/ui/sheet';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';

const UNCATEGORIZED_NAME = '未分類';

function formatPublishedAt(value: string | null) {
	return value ? value.slice(0, 10) : '—';
}

function formatFileSize(bytes: number) {
	if (bytes < 1024 * 1024) {
		return `${Math.max(1, Math.round(bytes / 1024))} KB`;
	}
	return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function sortCategories(categories: AdminCategory[]) {
	return [...categories].sort((a, b) => a.displayOrder - b.displayOrder || a.id - b.id);
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
	const [editingBook, setEditingBook] = useState<AdminBook | null>(null);
	const [mutatingBookId, setMutatingBookId] = useState<number | null>(null);
	const [newCategoryName, setNewCategoryName] = useState('');
	const [editingCategoryId, setEditingCategoryId] = useState<number | null>(null);
	const [editingCategoryName, setEditingCategoryName] = useState('');
	const [mutatingCategoryId, setMutatingCategoryId] = useState<number | null>(null);

	useEffect(() => {
		let isMounted = true;

		fetchAdminCategories()
			.then((loadedCategories) => {
				if (!isMounted) return;
				setCategories(sortCategories(loadedCategories));
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

	const changeBookState = (nextState: AdminBookState) => {
		if (nextState === bookState) return;
		setBooksLoading(true);
		setBooksError(null);
		setBookState(nextState);
	};

	const handleBookRegistration = async (values: BookRegistrationValues) => {
		const file = values.file[0];
		if (!file) {
			throw new Error('Book file is required');
		}
		const categoryId = values.categoryId ?? categories.find((category) => category.name === UNCATEGORIZED_NAME)?.id;
		if (!categoryId) {
			throw new Error('Uncategorized category is required');
		}

		const registeredBook = await registerAdminBook({
			title: values.title,
			authorName: values.authorName,
			publisher: values.publisher,
			publishedAt: values.publishedAt,
			categoryId,
			pageTurnDirection: values.pageTurnDirection,
			description: values.description,
			publicationScope: values.publicationScope,
			file,
		});
		changeBookState('active');
		setBooks((currentBooks) => [{ ...registeredBook, deletedAt: null }, ...currentBooks.filter((book) => book.id !== registeredBook.id)]);
		setIsSheetOpen(false);
		toast.success('書籍とファイルを登録しました。');
	};

	const handleBookUpdate = async (values: BookEditorValues) => {
		if (!editingBook) return;
		if (
			editingBook.publicationScope === 'admin_only' &&
			values.publicationScope === 'all_users' &&
			!window.confirm('この書籍を全ユーザー公開に変更しますか？')
		) {
			return;
		}

		const updated = await updateAdminBookMetadata(editingBook.id, values);
		setBooks((currentBooks) => currentBooks.map((book) => (
			book.id === editingBook.id ? { ...book, ...updated, file: book.file } : book
		)));
		setEditingBook(null);
		toast.success('書籍情報を更新しました。読書記録は保持されています。');
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

	const handleCreateCategory = async () => {
		const name = newCategoryName.trim();
		if (!name) return;
		setMutatingCategoryId(0);
		try {
			const created = await createAdminCategory(name);
			setCategories((current) => sortCategories([...current, created]));
			setNewCategoryName('');
			toast.success('カテゴリを追加しました。');
		} catch {
			toast.error('カテゴリの追加に失敗しました。同名カテゴリがないか確認してください。');
		} finally {
			setMutatingCategoryId(null);
		}
	};

	const handleRenameCategory = async (categoryId: number) => {
		const name = editingCategoryName.trim();
		if (!name) return;
		setMutatingCategoryId(categoryId);
		try {
			const updated = await renameAdminCategory(categoryId, name);
			setCategories((current) => sortCategories(current.map((category) => (category.id === categoryId ? updated : category))));
			setBooks((current) => current.map((book) => (
				book.category.id === categoryId ? { ...book, category: { ...book.category, name: updated.name } } : book
			)));
			setEditingCategoryId(null);
			setEditingCategoryName('');
			toast.success('カテゴリ名を変更しました。');
		} catch {
			toast.error('カテゴリ名の変更に失敗しました。');
		} finally {
			setMutatingCategoryId(null);
		}
	};

	const handleDeleteCategory = async (categoryId: number) => {
		setMutatingCategoryId(categoryId);
		try {
			const result = await deleteAdminCategory(categoryId);
			setCategories((current) => current.filter((category) => category.id !== categoryId));
			setBooks(await fetchAdminBooks(bookState));
			toast.success(`カテゴリを削除し、${result.movedBookCount}冊を未分類へ移動しました。`);
		} catch {
			toast.error('カテゴリの削除に失敗しました。');
		} finally {
			setMutatingCategoryId(null);
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
							<SheetDescription>タイトル・公開範囲・EPUB/PDFファイルが必須です。カテゴリ未選択時は未分類になります。</SheetDescription>
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

			<Sheet open={editingBook !== null} onOpenChange={(open) => { if (!open) setEditingBook(null); }}>
				<SheetContent className='w-full sm:max-w-xl'>
					<SheetHeader className='border-b px-5 py-5'>
						<SheetTitle className='text-xl'>書籍情報を編集</SheetTitle>
						<SheetDescription>書籍情報と公開範囲を更新します。書籍ファイルと読書記録は変更しません。</SheetDescription>
					</SheetHeader>
					{editingBook && (
						<BookEditor
							key={editingBook.id}
							book={editingBook}
							categories={categories}
							onSubmit={handleBookUpdate}
							onCancel={() => setEditingBook(null)}
						/>
					)}
				</SheetContent>
			</Sheet>

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
						<Button variant={bookState === 'active' ? 'default' : 'outline'} onClick={() => changeBookState('active')}>通常書籍</Button>
						<Button variant={bookState === 'deleted' ? 'default' : 'outline'} onClick={() => changeBookState('deleted')}>削除済み</Button>
					</div>
				</CardHeader>
				<CardContent className='overflow-x-auto'>
					{booksLoading && <p className='mb-4 text-sm text-muted-foreground'>書籍を読み込んでいます…</p>}
					{booksError && <p role='alert' className='mb-4 text-sm text-destructive'>{booksError}</p>}
					<Table className='min-w-[1040px]'>
						<TableHeader>
							<TableRow>
								<TableHead>タイトル</TableHead>
								<TableHead>著者</TableHead>
								<TableHead>カテゴリ</TableHead>
								<TableHead>出版日</TableHead>
								<TableHead>公開範囲</TableHead>
								<TableHead>ファイル</TableHead>
								<TableHead className='w-48'>操作</TableHead>
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
											<div className='flex gap-2'>
												<Button size='sm' variant='outline' onClick={() => setEditingBook(book)}>
													<Pencil />編集
												</Button>
												<Button size='sm' variant='outline' disabled={mutatingBookId === book.id} onClick={() => handleDelete(book.id)}>
													<Trash2 />削除
												</Button>
											</div>
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

			<Card>
				<CardHeader>
					<CardTitle>カテゴリ管理</CardTitle>
					<CardDescription>未分類は固定カテゴリです。使用中カテゴリを削除すると、その書籍は未分類へ移動します。</CardDescription>
				</CardHeader>
				<CardContent className='space-y-4'>
					<div className='flex flex-col gap-2 sm:flex-row'>
						<Input
							aria-label='新しいカテゴリ名'
							placeholder='新しいカテゴリ名'
							value={newCategoryName}
							onChange={(event) => setNewCategoryName(event.target.value)}
							maxLength={255}
						/>
						<Button onClick={handleCreateCategory} disabled={!newCategoryName.trim() || mutatingCategoryId !== null}>
							<Plus />カテゴリ追加
						</Button>
					</div>
					{categoriesLoading && <p className='text-sm text-muted-foreground'>カテゴリを読み込んでいます…</p>}
					{categoriesError && <p role='alert' className='text-sm text-destructive'>{categoriesError}</p>}
					<Table>
						<TableHeader>
							<TableRow>
								<TableHead>カテゴリ名</TableHead>
								<TableHead className='w-24'>順序</TableHead>
								<TableHead className='w-56'>操作</TableHead>
							</TableRow>
						</TableHeader>
						<TableBody>
							{categories.map((category) => {
								const protectedCategory = category.name === UNCATEGORIZED_NAME;
								const editing = editingCategoryId === category.id;
								return (
									<TableRow key={category.id}>
										<TableCell>
											{editing ? (
												<Input
													aria-label={`${category.name} の新しいカテゴリ名`}
													value={editingCategoryName}
													onChange={(event) => setEditingCategoryName(event.target.value)}
													maxLength={255}
												/>
											) : (
												<div className='flex items-center gap-2'>
													<span>{category.name}</span>
													{protectedCategory && <Badge variant='secondary'>固定</Badge>}
												</div>
											)}
										</TableCell>
										<TableCell>{category.displayOrder}</TableCell>
										<TableCell>
											{!protectedCategory && (editing ? (
												<div className='flex gap-2'>
													<Button size='sm' disabled={!editingCategoryName.trim() || mutatingCategoryId === category.id} onClick={() => handleRenameCategory(category.id)}>保存</Button>
													<Button size='sm' variant='outline' onClick={() => { setEditingCategoryId(null); setEditingCategoryName(''); }}>キャンセル</Button>
												</div>
											) : (
												<div className='flex gap-2'>
													<Button size='sm' variant='outline' onClick={() => { setEditingCategoryId(category.id); setEditingCategoryName(category.name); }}>
														<Pencil />名称変更
													</Button>
													<Button size='sm' variant='outline' disabled={mutatingCategoryId === category.id} onClick={() => handleDeleteCategory(category.id)}>
														<Trash2 />カテゴリ削除
													</Button>
												</div>
											))}
										</TableCell>
									</TableRow>
								);
							})}
						</TableBody>
					</Table>
				</CardContent>
			</Card>
		</main>
	);
}

export default Admin;
