import { useState } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { Controller, useForm } from 'react-hook-form';
import * as z from 'zod';

import type { AdminCategory, PublicationScope } from '@/api/admin';
import { Button } from '@/components/ui/button';
import { Field, FieldError, FieldGroup, FieldLabel } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';

const MAX_BOOK_FILE_SIZE = 200 * 1024 * 1024;

const bookRegistrationSchema = z.object({
	title: z.string().trim().min(1, 'タイトルを入力してください。').max(255),
	authorName: z.string().trim().max(255),
	publisher: z.string().trim().max(255),
	publishedAt: z.string(),
	categoryId: z.number().int().positive('カテゴリを選択してください。'),
	pageTurnDirection: z.enum(['ltr', 'rtl']),
	description: z.string().trim().max(1000, '説明は1000文字以内で入力してください。'),
	publicationScope: z.enum(['all_users', 'admin_only']),
	file: z
		.custom<FileList>()
		.refine((files) => files instanceof FileList && files.length === 1, 'EPUBまたはPDFを1ファイル選択してください。')
		.refine((files) => !files?.[0] || files[0].size <= MAX_BOOK_FILE_SIZE, 'ファイルは200MB以下にしてください。')
		.refine((files) => {
			const file = files?.[0];
			if (!file) return true;
			const name = file.name.toLowerCase();
			const type = file.type.toLowerCase();
			return (name.endsWith('.pdf') && type === 'application/pdf') || (name.endsWith('.epub') && type === 'application/epub+zip');
		}, 'EPUBまたはPDFのみ登録できます。'),
});

export type BookRegistrationValues = z.infer<typeof bookRegistrationSchema>;

type BookRegistarProps = {
	categories: AdminCategory[];
	categoriesLoading: boolean;
	categoriesError: string | null;
	onSubmit: (values: BookRegistrationValues) => Promise<void>;
};

export default function BookRegistar({ categories, categoriesLoading, categoriesError, onSubmit }: BookRegistarProps) {
	const [submitError, setSubmitError] = useState<string | null>(null);
	const form = useForm<BookRegistrationValues>({
		resolver: zodResolver(bookRegistrationSchema),
		defaultValues: {
			title: '',
			authorName: '',
			publisher: '',
			publishedAt: '',
			categoryId: undefined,
			pageTurnDirection: 'ltr',
			description: '',
			publicationScope: undefined,
		},
	});

	const handleSubmit = form.handleSubmit(async (values) => {
		setSubmitError(null);
		try {
			await onSubmit(values);
			form.reset();
		} catch {
			setSubmitError('書籍の登録に失敗しました。入力内容またはファイルを確認して再度お試しください。');
		}
	});

	const canSubmit = !categoriesLoading && !categoriesError && categories.length > 0 && !form.formState.isSubmitting;

	return (
		<form id='book-registration-form' onSubmit={handleSubmit} className='flex min-h-0 flex-1 flex-col'>
			<div className='flex-1 overflow-y-auto px-5 pb-6'>
				{categoriesLoading && <p className='mb-5 rounded-md border border-border bg-muted px-3 py-2 text-sm text-muted-foreground'>カテゴリを読み込んでいます…</p>}
				{categoriesError && <p role='alert' className='mb-5 rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive'>{categoriesError}</p>}
				{!categoriesLoading && !categoriesError && categories.length === 0 && (
					<p role='alert' className='mb-5 rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive'>利用可能なカテゴリがありません。</p>
				)}
				{submitError && <p role='alert' className='mb-5 rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive'>{submitError}</p>}

				<FieldGroup className='gap-5'>
					<Field data-invalid={Boolean(form.formState.errors.title)}>
						<FieldLabel htmlFor='book-title'>タイトル *</FieldLabel>
						<Input id='book-title' placeholder='例: TypeScript API開発入門' aria-invalid={Boolean(form.formState.errors.title)} {...form.register('title')} />
						<FieldError errors={[form.formState.errors.title]} />
					</Field>

					<Field>
						<FieldLabel htmlFor='book-author'>著者</FieldLabel>
						<Input id='book-author' placeholder='著者名' {...form.register('authorName')} />
					</Field>

					<div className='grid grid-cols-1 gap-5 sm:grid-cols-2'>
						<Field data-invalid={Boolean(form.formState.errors.categoryId)}>
							<FieldLabel>カテゴリ *</FieldLabel>
							<Controller
								name='categoryId'
								control={form.control}
								render={({ field }) => (
									<Select value={field.value ? String(field.value) : undefined} onValueChange={(value) => field.onChange(Number(value))} disabled={!canSubmit}>
										<SelectTrigger className='w-full' aria-label='カテゴリ'>
											<SelectValue placeholder={categoriesLoading ? '読み込み中…' : 'カテゴリを選択'} />
										</SelectTrigger>
										<SelectContent>
											{categories.map((category) => (
												<SelectItem key={category.id} value={String(category.id)}>
													{category.name}
												</SelectItem>
											))}
										</SelectContent>
									</Select>
								)}
							/>
							<FieldError errors={[form.formState.errors.categoryId]} />
						</Field>

						<Field>
							<FieldLabel>ページ方向</FieldLabel>
							<Controller
								name='pageTurnDirection'
								control={form.control}
								render={({ field }) => (
									<Select value={field.value} onValueChange={field.onChange}>
										<SelectTrigger className='w-full' aria-label='ページ方向'>
											<SelectValue />
										</SelectTrigger>
										<SelectContent>
											<SelectItem value='ltr'>左から右</SelectItem>
											<SelectItem value='rtl'>右から左</SelectItem>
										</SelectContent>
									</Select>
								)}
							/>
						</Field>
					</div>

					<Field>
						<FieldLabel htmlFor='book-publisher'>出版社</FieldLabel>
						<Input id='book-publisher' placeholder='出版社名' {...form.register('publisher')} />
					</Field>

					<Field>
						<FieldLabel htmlFor='book-published-at'>出版日</FieldLabel>
						<Input id='book-published-at' type='date' {...form.register('publishedAt')} />
					</Field>

					<Field>
						<FieldLabel htmlFor='book-description'>説明</FieldLabel>
						<textarea
							id='book-description'
							rows={4}
							className='w-full resize-none rounded-md border border-input bg-transparent px-3 py-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50'
							placeholder='書籍の概要を入力'
							{...form.register('description')}
						/>
						<FieldError errors={[form.formState.errors.description]} />
					</Field>

					<Field data-invalid={Boolean(form.formState.errors.publicationScope)}>
						<FieldLabel>公開範囲 *</FieldLabel>
						<Controller
							name='publicationScope'
							control={form.control}
							render={({ field }) => (
								<Select value={field.value} onValueChange={(value) => field.onChange(value as PublicationScope)}>
									<SelectTrigger className='w-full' aria-label='公開範囲'>
										<SelectValue placeholder='公開範囲を選択' />
									</SelectTrigger>
									<SelectContent>
										<SelectItem value='all_users'>全ユーザー公開</SelectItem>
										<SelectItem value='admin_only'>管理者のみ</SelectItem>
									</SelectContent>
								</Select>
							)}
						/>
						<FieldError errors={[form.formState.errors.publicationScope]} />
					</Field>

					<Field data-invalid={Boolean(form.formState.errors.file)}>
						<FieldLabel htmlFor='book-file'>書籍ファイル *</FieldLabel>
						<Input id='book-file' type='file' accept='.epub,.pdf,application/epub+zip,application/pdf' {...form.register('file')} />
						<p className='text-xs text-muted-foreground'>EPUBまたはPDF（最大200MB）。登録成功時に書籍情報と同時に保存します。</p>
						<FieldError errors={[form.formState.errors.file]} />
					</Field>
				</FieldGroup>
			</div>

			<div className='border-t bg-card p-5'>
				<Button type='submit' className='w-full' disabled={!canSubmit}>
					{form.formState.isSubmitting ? '登録中…' : '登録する'}
				</Button>
			</div>
		</form>
	);
}
