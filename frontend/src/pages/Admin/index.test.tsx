import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { render } from 'vitest-browser-react';

const mocks = vi.hoisted(() => ({
	fetchAdminBooks: vi.fn(),
	fetchAdminCategories: vi.fn(),
	registerAdminBook: vi.fn(),
	softDeleteAdminBook: vi.fn(),
	restoreAdminBook: vi.fn(),
}));

vi.mock('@/api/admin', () => ({
	fetchAdminBooks: mocks.fetchAdminBooks,
	fetchAdminCategories: mocks.fetchAdminCategories,
	registerAdminBook: mocks.registerAdminBook,
	softDeleteAdminBook: mocks.softDeleteAdminBook,
	restoreAdminBook: mocks.restoreAdminBook,
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

const activeBook = {
	id: 10,
	title: '保存済みPDF',
	authorName: null,
	publisher: null,
	publishedAt: null,
	categoryId: 17,
	pageTurnDirection: 'ltr',
	description: null,
	deletedAt: null,
	category: { id: 17, name: '技術書' },
	publicationScope: 'admin_only',
	file: { id: 20, extension: 'pdf', mimeType: 'application/pdf', originalFileName: 'saved.pdf', fileSize: 2048 },
};

const deletedBook = {
	...activeBook,
	deletedAt: '2026-08-24T10:00:00.000Z',
};

describe('Admin persisted book management', () => {
	beforeEach(() => {
		mocks.fetchAdminCategories.mockResolvedValue([{ id: 17, name: '技術書' }]);
		mocks.fetchAdminBooks.mockResolvedValue([]);
		mocks.softDeleteAdminBook.mockResolvedValue({ id: 10, deletedAt: '2026-08-24T10:00:00.000Z' });
		mocks.restoreAdminBook.mockResolvedValue({ id: 10, deletedAt: null });
		mocks.registerAdminBook.mockResolvedValue({
			id: 21,
			title: 'EPUB実登録',
			authorName: 'Author',
			publisher: null,
			publishedAt: null,
			categoryId: 17,
			pageTurnDirection: 'ltr',
			description: null,
			deletedAt: null,
			category: { id: 17, name: '技術書' },
			publicationScope: 'all_users',
			file: { id: 31, extension: 'epub', mimeType: 'application/epub+zip', originalFileName: 'book.epub', fileSize: 1024 },
		});
	});

	afterEach(() => {
		vi.restoreAllMocks();
		mocks.fetchAdminBooks.mockReset();
		mocks.fetchAdminCategories.mockReset();
		mocks.registerAdminBook.mockReset();
		mocks.softDeleteAdminBook.mockReset();
		mocks.restoreAdminBook.mockReset();
	});

	test('再読み込み時にbackendの通常書籍を表示する', async () => {
		mocks.fetchAdminBooks.mockResolvedValue([activeBook]);

		const { getByText } = await render(<Admin />);
		await vi.waitFor(() => expect(mocks.fetchAdminBooks).toHaveBeenCalledWith('active'));
		await expect.element(getByText('保存済みPDF')).toBeInTheDocument();
		await expect.element(getByText('saved.pdf')).toBeInTheDocument();
		await expect.element(getByText('管理者のみ')).toBeInTheDocument();
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

	test('通常書籍を論理削除し、削除済み一覧から復元できる', async () => {
		mocks.fetchAdminBooks.mockImplementation(async (state: string) => (state === 'deleted' ? [deletedBook] : [activeBook]));
		const { getByRole, getByText } = await render(<Admin />);

		await vi.waitFor(() => expect(mocks.fetchAdminBooks).toHaveBeenCalledWith('active'));
		await getByRole('button', { name: '削除' }).click();
		await vi.waitFor(() => expect(mocks.softDeleteAdminBook).toHaveBeenCalledWith(10));
		await expect.element(getByText('該当する書籍はありません。')).toBeInTheDocument();

		await getByRole('button', { name: '削除済み' }).click();
		await vi.waitFor(() => expect(mocks.fetchAdminBooks).toHaveBeenCalledWith('deleted'));
		await expect.element(getByText('保存済みPDF')).toBeInTheDocument();
		await getByRole('button', { name: '復元' }).click();
		await vi.waitFor(() => expect(mocks.restoreAdminBook).toHaveBeenCalledWith(10));
		await expect.element(getByText('該当する書籍はありません。')).toBeInTheDocument();
	});
});
