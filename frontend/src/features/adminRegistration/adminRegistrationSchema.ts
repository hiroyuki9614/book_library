import { z } from 'zod';

export const adminRegistrationSchema = z
	.object({
		name: z.string().trim().min(1, '表示名を入力してください。').max(255, '表示名は255文字以内で入力してください。'),
		email: z.string().trim().email('正しいメールアドレスを入力してください。').max(254, 'メールアドレスは254文字以内で入力してください。'),
		password: z.string().min(8, 'パスワードは8文字以上で入力してください。'),
		passwordConfirmation: z.string().min(1, '確認用パスワードを入力してください。'),
	})
	.refine((values) => values.password === values.passwordConfirmation, {
		message: 'パスワードが一致しません。',
		path: ['passwordConfirmation'],
	});

export type AdminRegistrationFormValues = z.infer<typeof adminRegistrationSchema>;
