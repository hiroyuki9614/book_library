import { zodResolver } from '@hookform/resolvers/zod';
import { Controller, useForm } from 'react-hook-form';
import * as z from 'zod';

import { Button } from '@/components/ui/button';
import { Field, FieldError, FieldGroup, FieldLabel } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';

const bookRegistrationSchema = z.object({
	title: z.string().trim().min(1, 'タイトルを入力してください。').max(255),
	authorName: z.string().trim().max(255),
	publisher: z.string().trim().max(255),
	publishedAt: z.string(),
	categoryName: z.enum(['小説', '技術書', 'ビジネス', '歴史', 'その他']),
	pageTurnDirection: z.enum(['ltr', 'rtl']),
	description: z.string().trim().max(1000, '説明は1000文字以内で入力してください。'),
	file: z.custom<FileList>().optional(),
});

export type BookRegistrationValues = z.infer<typeof bookRegistrationSchema>;

type BookRegistarProps = {
	onSubmit: (values: BookRegistrationValues) => void | Promise<void>;
};

export default function BookRegistar({ onSubmit }: BookRegistarProps) {
	const form = useForm<BookRegistrationValues>({
		resolver: zodResolver(bookRegistrationSchema),
		defaultValues: {
			title: '',
			authorName: '',
			publisher: '',
			publishedAt: '',
			categoryName: 'その他',
			pageTurnDirection: 'ltr',
			description: '',
		},
	});

	const handleSubmit = form.handleSubmit(async (values) => {
		await onSubmit(values);
		form.reset();
	});

	return (
		<form id='book-registration-form' onSubmit={handleSubmit} className='flex min-h-0 flex-1 flex-col'>
			<div className='flex-1 overflow-y-auto px-5 pb-6'>
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
						<Field>
							<FieldLabel>カテゴリ</FieldLabel>
							<Controller
								name='categoryName'
								control={form.control}
								render={({ field }) => (
									<Select value={field.value} onValueChange={field.onChange}>
										<SelectTrigger className='w-full' aria-label='カテゴリ'>
											<SelectValue />
										</SelectTrigger>
										<SelectContent>
											{['小説', '技術書', 'ビジネス', '歴史', 'その他'].map((category) => (
												<SelectItem key={category} value={category}>
													{category}
												</SelectItem>
											))}
										</SelectContent>
									</Select>
								)}
							/>
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

					<Field>
						<FieldLabel htmlFor='book-file'>書籍ファイル</FieldLabel>
						<Input id='book-file' type='file' accept='.epub,.pdf,application/epub+zip,application/pdf' {...form.register('file')} />
						<p className='text-xs text-muted-foreground'>EPUBまたはPDF（仮実装ではファイル名のみ保持します）</p>
					</Field>
				</FieldGroup>
			</div>

			<div className='border-t bg-card p-5'>
				<Button type='submit' className='w-full'>
					登録する
				</Button>
			</div>
		</form>
	);
}
