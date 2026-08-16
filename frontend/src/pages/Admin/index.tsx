import { useEffect, useState } from 'react';
import { BookOpen, Library, Plus } from 'lucide-react';
import { toast } from 'sonner';

import { createAdminBook, fetchAdminCategories, type AdminBook, type AdminCategory } from '@/api/admin';
import BookRegistar, { type BookRegistrationValues } from '@/components/forms/BookRegistar';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from '@/components/ui/sheet';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';

function formatPublishedAt(value: string | null) {
	return value ? value.slice(0, 10) : '—';
}

function Admin() {
	const [books, setBooks] = useState<AdminBook[]>([]);
	const [categories, setCategories] = useState<AdminCategory[]>([]);
	const [categoriesLoading, setCategoriesLoading] = useState(true);
	const [categoriesError, setCategoriesError] = useState<string | null>(null);
	const [isSheetOpen, setIsSheetOpen] = useState(false);

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

	const handleBookRegistration = async (values: BookRegistrationValues) => {
		const registeredBook = await createAdminBook(values);
		setBooks((currentBooks) => [registeredBook, ...currentBooks]);
		setIsSheetOpen(false);
		toast.success('書籍を登録しました。');
	};

	return (
		<main className='mx-auto w-full max-w-7xl space-y-6 p-4 md:p-8'>
			<header className='flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between'>
				<div>
					<p className='mb-1 text-sm font-medium text-muted-foreground'>ADMIN CONSOLE</p>
					<h1 className='text-3xl font-semibold tracking-tight'>書籍管理</h1>
					<p className='mt-2 text-sm text-muted-foreground'>書籍メタデータを登録・管理します。</p>
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
							<SheetDescription>タイトルとカテゴリが必須です。ファイル登録は別工程です。</SheetDescription>
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
							<CardDescription>この画面で登録した書籍</CardDescription>
							<CardTitle className='mt-2 text-3xl'>{books.length}</CardTitle>
						</div>
						<Library className='size-7 text-muted-foreground' />
					</CardHeader>
				</Card>
				<Card>
					<CardHeader className='flex-row items-center justify-between'>
						<div>
							<CardDescription>ファイル登録済み</CardDescription>
							<CardTitle className='mt-2 text-3xl'>0</CardTitle>
						</div>
						<BookOpen className='size-7 text-muted-foreground' />
					</CardHeader>
				</Card>
			</section>

			<Card>
				<CardHeader>
					<CardTitle>書籍一覧</CardTitle>
					<CardDescription>この画面でバックエンド登録に成功した書籍を表示します。</CardDescription>
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
									<TableCell>{book.authorName ?? '未設定'}</TableCell>
									<TableCell>{book.category.name}</TableCell>
									<TableCell>{formatPublishedAt(book.publishedAt)}</TableCell>
									<TableCell>{book.pageTurnDirection === 'rtl' ? '右から左' : '左から右'}</TableCell>
									<TableCell><Badge variant='secondary'>未登録（別工程）</Badge></TableCell>
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
