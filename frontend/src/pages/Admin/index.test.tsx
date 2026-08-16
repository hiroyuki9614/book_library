import { afterEach, expect, test, vi } from 'vitest';
import { render } from 'vitest-browser-react';

import Admin from './index';

afterEach(() => {
	vi.unstubAllEnvs();
	vi.restoreAllMocks();
});

function mockCategoryResponse() {
	return new Response(JSON.stringify({ categories: [{ id: 17, name: '技術書' }] }), { status: 200 });
}

test('backendのカテゴリを選択して、成功したAPI応答の書籍だけを一覧へ追加する', async () => {
	vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
	vi.spyOn(globalThis, 'fetch')
		.mockResolvedValueOnce(mockCategoryResponse())
		.mockResolvedValueOnce(new Response(JSON.stringify({
			id: 21,
			title: 'テスト駆動開発入門',
			authorName: 'テスト著者',
			publisher: null,
			publishedAt: null,
			categoryId: 17,
			pageTurnDirection: 'ltr',
			description: null,
			category: { id: 17, name: '技術書' },
		}), { status: 201 }));

	const { getByLabelText, getByRole, getByText } = await render(<Admin />);

	await getByRole('button', { name: '新しい書籍を登録' }).click();
	await expect.element(getByRole('heading', { name: '書籍を登録' })).toBeInTheDocument();

	await getByLabelText('タイトル *').fill('テスト駆動開発入門');
	await getByLabelText('著者').fill('テスト著者');
	await getByRole('combobox', { name: 'カテゴリ' }).click();
	await getByRole('option', { name: '技術書' }).click();
	await getByRole('button', { name: '登録する' }).click();

	await expect.element(getByText('テスト駆動開発入門')).toBeInTheDocument();
	await expect.element(getByText('テスト著者')).toBeInTheDocument();
	await expect.element(getByRole('heading', { name: '書籍を登録' })).not.toBeInTheDocument();
	expect(fetch).toHaveBeenNthCalledWith(2, 'http://localhost:3000/api/v1/admin/books', expect.objectContaining({
		method: 'POST',
		body: expect.stringContaining('"categoryId":17'),
	}));
});

test('登録API失敗時は成功表示せずフォームを開いたままにする', async () => {
	vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
	vi.spyOn(globalThis, 'fetch')
		.mockResolvedValueOnce(mockCategoryResponse())
		.mockResolvedValueOnce(new Response(JSON.stringify({ code: 'INVALID_CATEGORY' }), { status: 400 }));

	const { getByLabelText, getByRole } = await render(<Admin />);
	await getByRole('button', { name: '新しい書籍を登録' }).click();
	await getByLabelText('タイトル *').fill('失敗する登録');
	await getByRole('combobox', { name: 'カテゴリ' }).click();
	await getByRole('option', { name: '技術書' }).click();
	await getByRole('button', { name: '登録する' }).click();

	await expect.element(getByRole('alert')).toHaveTextContent('書籍の登録に失敗しました');
	await expect.element(getByRole('heading', { name: '書籍を登録' })).toBeInTheDocument();
});

test('ファイル入力は表示せず、別工程であることを明示する', async () => {
	vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
	vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(mockCategoryResponse());

	const { getByRole, getByText } = await render(<Admin />);
	await getByRole('button', { name: '新しい書籍を登録' }).click();

	await expect.element(getByText('ファイル登録は別工程です。この画面ではメタデータのみ保存します。')).toBeInTheDocument();
});
