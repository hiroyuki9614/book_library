import { expect, test } from 'vitest';
import { render } from 'vitest-browser-react';

import Admin from './index';

test('Sheetから書籍を登録して一覧へ追加できる', async () => {
	const { getByLabelText, getByRole, getByText } = await render(<Admin />);

	await getByRole('button', { name: '新しい書籍を登録' }).click();
	await expect.element(getByRole('heading', { name: '書籍を登録' })).toBeInTheDocument();

	await getByLabelText('タイトル *').fill('テスト駆動開発入門');
	await getByLabelText('著者').fill('テスト著者');
	await getByRole('button', { name: '登録する' }).click();

	await expect.element(getByText('テスト駆動開発入門')).toBeInTheDocument();
	await expect.element(getByText('テスト著者')).toBeInTheDocument();
	await expect.element(getByRole('heading', { name: '書籍を登録' })).not.toBeInTheDocument();
});
