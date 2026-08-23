import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { render } from 'vitest-browser-react';

const mocks = vi.hoisted(() => ({
	fetchAdminCategories: vi.fn(),
	registerAdminBook: vi.fn(),
}));

vi.mock('@/api/admin', () => ({
	fetchAdminCategories: mocks.fetchAdminCategories,
	registerAdminBook: mocks.registerAdminBook,
}));

vi.mock('@/components/forms/BookRegistar', () => ({
	default: ({ onSubmit }: { onSubmit: (values: any) => Promise<void> }) => (
		<button
			type='button'
			onClick={() => onSubmit({
				title: 'EPUB実登録',
				authorName: 'Author',
				publisher: '',
				publishedAt: '',
				categoryId: 17,
				pageTurnDirection: 'ltr',
				description: '',
				publicationScope: 'all_users',
				file: [new File(['epub'], 'book.epub', { type: 'application/epub+zip' })],
			})}
		>
			テスト登録
		</button>
	),
}));

import Admin from './index';

describe('Admin full book registration', () => {
	beforeEach(() => {
		mocks.fetchAdminCategories.mockResolvedValue([{ id: 17, name: '技術書' }]);
		mocks.registerAdminBook.mockResolvedValue({
			id: 21,
			title: 'EPUB実登録',
			authorName: 'Author',
			publisher: null,
			publishedAt: null,
			categoryId: 17,
			pageTurnDirection: 'ltr',
			description: null,
			category: { id: 17, name: '技術書' },
			publicationScope: 'all_users',
			file: { id: 31, extension: 'epub', mimeType: 'application/epub+zip', originalFileName: 'book.epub', fileSize: 1024 },
		});
	});

	afterEach(() => {
		vi.restoreAllMocks();
		mocks.fetchAdminCategories.mockReset();
		mocks.registerAdminBook.mockReset();
	});

	test('管理画面からEPUBを実登録し、成功レスポンスを一覧へ反映する', async () => {
		const { getByRole, getByText } = await render(<Admin />);

		await getByRole('button', { name: '新しい書籍を登録' }).click();
		await getByRole('button', { name: 'テスト登録' }).click();

		await vi.waitFor(() => expect(mocks.registerAdminBook).toHaveBeenCalledTimes(1));
		expect(mocks.registerAdminBook).toHaveBeenCalledWith(expect.objectContaining({
			title: 'EPUB実登録',
			categoryId: 17,
			publicationScope: 'all_users',
			file: expect.objectContaining({ name: 'book.epub', type: 'application/epub+zip' }),
		}));
		await expect.element(getByText('EPUB実登録')).toBeInTheDocument();
		await expect.element(getByText('book.epub')).toBeInTheDocument();
		await expect.element(getByText('全ユーザー公開')).toBeInTheDocument();
	});
});
