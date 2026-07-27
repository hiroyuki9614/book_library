import { describe, expect, test } from 'vitest';
import { adminRegistrationSchema } from './adminRegistrationSchema';

const validValues = {
	name: '管理者',
	email: 'admin@example.com',
	password: 'password123',
	passwordConfirmation: 'password123',
};

describe('adminRegistrationSchema', () => {
	test('有効な管理者登録情報を受け付ける', () => {
		expect(adminRegistrationSchema.safeParse(validValues).success).toBe(true);
	});

	test('8文字未満のパスワードを拒否する', () => {
		expect(
			adminRegistrationSchema.safeParse({
				...validValues,
				password: 'short',
				passwordConfirmation: 'short',
			}).success,
		).toBe(false);
	});

	test('確認用パスワードが一致しない場合は拒否する', () => {
		const result = adminRegistrationSchema.safeParse({
			...validValues,
			passwordConfirmation: 'different-password',
		});

		expect(result.success).toBe(false);
		if (!result.success) {
			expect(result.error.issues[0]?.path).toEqual(['passwordConfirmation']);
		}
	});
});
