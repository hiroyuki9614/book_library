import { useEffect, useState, type FormEvent } from 'react';

import type { AdminBook, AdminBookMetadataInput, AdminCategory, PublicationScope } from '@/api/admin';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';

export type BookEditorValues = AdminBookMetadataInput;

type BookEditorProps = {
	book: AdminBook;
	categories: AdminCategory[];
	onSubmit: (values: BookEditorValues) => Promise<void>;
	onCancel: () => void;
};

export default function BookEditor({ book, categories, onSubmit, onCancel }: BookEditorProps) {
	const [title, setTitle] = useState(book.title);
	const [authorName, setAuthorName] = useState(book.authorName ?? '');
	const [publisher, setPublisher] = useState(book.publisher ?? '');
	const [publishedAt, setPublishedAt] = useState(book.publishedAt?.slice(0, 10) ?? '');
	const [categoryId, setCategoryId] = useState(book.categoryId);
	const [pageTurnDirection, setPageTurnDirection] = useState<'ltr' | 'rtl'>(book.pageTurnDirection);
	const [description, setDescription] = useState(book.description ?? '');
	const [publicationScope, setPublicationScope] = useState<PublicationScope>(book.publicationScope);
	const [submitting, setSubmitting] = useState(false);
	const [submitError, setSubmitError] = useState<string | null>(null);

	useEffect(() => {
		setTitle(book.title);
		setAuthorName(book.authorName ?? '');
		setPublisher(book.publisher ?? '');
		setPublishedAt(book.publishedAt?.slice(0, 10) ?? '');
		setCategoryId(book.categoryId);
		setPageTurnDirection(book.pageTurnDirection);
		setDescription(book.description ?? '');
		setPublicationScope(book.publicationScope);
		setSubmitError(null);
	}, [book]);

	const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
		event.preventDefault();
		if (!title.trim() || !categoryId) return;
		setSubmitting(true);
		setSubmitError(null);
		try {
			await onSubmit({
				title,
				authorName,
				publisher,
				publishedAt,
				categoryId,
				pageTurnDirection,
				description,
				publicationScope,
			});
		} catch {
			setSubmitError('書籍情報の更新に失敗しました。入力内容を確認してください。');
		} finally {
			setSubmitting(false);
		}
	};

	return (
		<form onSubmit={handleSubmit} className='flex min-h-0 flex-1 flex-col'>
			<div className='flex-1 space-y-5 overflow-y-auto px-5 pb-6'>
				{submitError && <p role='alert' className='rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive'>{submitError}</p>}

				<div className='space-y-2'>
					<label htmlFor='edit-book-title' className='text-sm font-medium'>タイトル *</label>
					<Input id='edit-book-title' value={title} onChange={(event) => setTitle(event.target.value)} maxLength={255} required />
				</div>

				<div className='space-y-2'>
					<label htmlFor='edit-book-author' className='text-sm font-medium'>著者</label>
					<Input id='edit-book-author' value={authorName} onChange={(event) => setAuthorName(event.target.value)} maxLength={255} />
				</div>

				<div className='grid gap-5 sm:grid-cols-2'>
					<div className='space-y-2'>
						<label className='text-sm font-medium'>カテゴリ *</label>
						<Select value={String(categoryId)} onValueChange={(value) => setCategoryId(Number(value))}>
							<SelectTrigger className='w-full' aria-label='編集カテゴリ'>
								<SelectValue />
							</SelectTrigger>
							<SelectContent>
								{categories.map((category) => (
									<SelectItem key={category.id} value={String(category.id)}>{category.name}</SelectItem>
								))}
							</SelectContent>
						</Select>
					</div>

					<div className='space-y-2'>
						<label className='text-sm font-medium'>ページ方向</label>
						<Select value={pageTurnDirection} onValueChange={(value) => setPageTurnDirection(value as 'ltr' | 'rtl')}>
							<SelectTrigger className='w-full' aria-label='編集ページ方向'><SelectValue /></SelectTrigger>
							<SelectContent>
								<SelectItem value='ltr'>左から右</SelectItem>
								<SelectItem value='rtl'>右から左</SelectItem>
							</SelectContent>
						</Select>
					</div>
				</div>

				<div className='space-y-2'>
					<label htmlFor='edit-book-publisher' className='text-sm font-medium'>出版社</label>
					<Input id='edit-book-publisher' value={publisher} onChange={(event) => setPublisher(event.target.value)} maxLength={255} />
				</div>

				<div className='space-y-2'>
					<label htmlFor='edit-book-published-at' className='text-sm font-medium'>出版日</label>
					<Input id='edit-book-published-at' type='date' value={publishedAt} onChange={(event) => setPublishedAt(event.target.value)} />
				</div>

				<div className='space-y-2'>
					<label htmlFor='edit-book-description' className='text-sm font-medium'>説明</label>
					<textarea
						id='edit-book-description'
						rows={4}
						className='w-full resize-none rounded-md border border-input bg-transparent px-3 py-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50'
						value={description}
						onChange={(event) => setDescription(event.target.value)}
					/>
				</div>

				<div className='space-y-2'>
					<label className='text-sm font-medium'>公開範囲 *</label>
					<Select value={publicationScope} onValueChange={(value) => setPublicationScope(value as PublicationScope)}>
						<SelectTrigger className='w-full' aria-label='編集公開範囲'><SelectValue /></SelectTrigger>
						<SelectContent>
							<SelectItem value='all_users'>全ユーザー公開</SelectItem>
							<SelectItem value='admin_only'>管理者のみ</SelectItem>
						</SelectContent>
					</Select>
				</div>
			</div>

			<div className='flex gap-2 border-t bg-card p-5'>
				<Button type='submit' className='flex-1' disabled={submitting || !title.trim()}>{submitting ? '更新中…' : '更新する'}</Button>
				<Button type='button' variant='outline' onClick={onCancel} disabled={submitting}>キャンセル</Button>
			</div>
		</form>
	);
}
