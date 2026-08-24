import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { render } from 'vitest-browser-react';

const mocks = vi.hoisted(() => ({
	createAdminCategory: vi.fn(),
	deleteAdminCategory: vi.fn(),
	fetchAdminBooks: vi.fn(),
	fetchAdminCategories: vi.fn(),
	registerAdminBook: vi.fn(),
	renameAdminCategory: vi.fn(),
	softDeleteAdminBook: vi.fn(),
	restoreAdminBook: vi.fn(),
}));

vi.mock('@/api/admin', () => ({
	createAdminCategory: mocks.createAdminCategory,
	deleteAdminCategory: mocks.deleteAdminCategory,
	fetchAdminBooks: mocks.fetchAdminBooks,
	fetchAdminCategories: mocks.fetchAdminCategories,
	registerAdminBook: mocks.registerAdminBook,
	renameAdminCategory: mocks.renameAdminCategory,
	softDeleteAdminBook: mocks.softDeleteAdminBook,
	restoreAdminBook: mocks.restoreAdminBook,
}));

vi.mock('@/components/forms/BookRegistar', () => ({
	default: ({ onSubmit }: { onSubmit: (values: any) => Promise<void> }) => (
		<div>
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
			<button
				type='button'
				onClick={() => onSubmit({
					title: '未分類登録',
					authorName: '',
					publisher: '',
					publishedAt: '',
					categoryId: undefined,
					pageTurnDirection: 'ltr',
					description: '',
					publicationScope: 'admin_only',
					file: [new File(['epub'], 'uncategorized.epub', { type: 'application/epub+zip' })],
				})}
			>
				未分類テスト登録
			</button>
		</div>
	),
}));

import Admin from './index';

const uncategorized = { id: 1, name: '未分類', displayOrder: 0, isActive: true };
const techCategory = { id: 17, name: '技術書', displayOrder: 1, isActive: true };

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
		mocks.fetchAdminCategories.mockResolvedValue([uncategorized, techCategory]);
		mocks.fetchAdminBooks.mockResolvedValue([]);
		mocks.createAdminCategory.mockResolvedValue({ id: 18, name: '科学', displayOrder: 2, isActive: true });
		mocks.renameAdminCategory.mockResolvedValue({ id: 17, name: '開発', displayOrder: 1, isActive: true });
		mocks.deleteAdminCategory.mockResolvedValue({ movedBookCount: 1, category: { id: 17, name: '技術書', isActive: false } });
		mocks.softDeleteAdminBook.mockResolvedValue({ id: 10, deletedAt: '2026-08-24T10:00:00.000Z' });
		mocks.restoreAdminBook.mockResolvedValue({ id: 10, deletedAt: null });
		mocks.registerAdminBook.mockImplementation(async (input: { title: string; categoryId: number; file: File; publicationScope: string }) => ({
			id: 21,
			title: input.title,
			authorName: input.title === 'EPUB実登録' ? 'Author' : null,
			publisher: null,
			publishedAt: null,
			categoryId: input.categoryId,
			pageTurnDirection: 'ltr',
			description: null,
			deletedAt: null,
			category: input.categoryId === 1 ? { id: 1, name: '未分類' } : { id: 17, name: '技術書' },
			publicationScope: input.publicationScope,
			file: { id: 31, extension: 'epub', mimeType: 'application/epub+zip', originalFileName: input.file.name, fileSize: 1024 },
		}));
	});

	afterEach(() => {
		vi.restoreAllMocks();
		for (const mock of Object.values(mocks)) mock.mockReset();
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
		await getByRole('button', { name: 'テスト登録', exact: true }).click();

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

	test('カテゴリ未選択時は未分類で登録する', async () => {
		const { getByRole } = await render(<Admin />);
		await getByRole('button', { name: '新しい書籍を登録' }).click();
		await getByRole('button', { name: '未分類テスト登録' }).click();

		await vi.waitFor(() => expect(mocks.registerAdminBook).toHaveBeenCalledTimes(1));
		expect(mocks.registerAdminBook).toHaveBeenCalledWith(expect.objectContaining({
			title: '未分類登録',
			categoryId: 1,
		}));
	});

	test('通常書籍を論理削除し、削除済み一覧から復元できる', async () => {
		mocks.fetchAdminBooks.mockImplementation(async (state: string) => (state === 'deleted' ? [deletedBook] : [activeBook]));
		const { getByRole, getByText } = await render(<Admin />);

		await vi.waitFor(() => expect(mocks.fetchAdminBooks).toHaveBeenCalledWith('active'));
		await getByRole('button', { name: '削除', exact: true }).click();
		await vi.waitFor(() => expect(mocks.softDeleteAdminBook).toHaveBeenCalledWith(10));
		await expect.element(getByText('該当する書籍はありません。')).toBeInTheDocument();

		await getByRole('button', { name: '削除済み' }).click();
		await vi.waitFor(() => expect(mocks.fetchAdminBooks).toHaveBeenCalledWith('deleted'));
		await expect.element(getByText('保存済みPDF')).toBeInTheDocument();
		await getByRole('button', { name: '復元', exact: true }).click();
		await vi.waitFor(() => expect(mocks.restoreAdminBook).toHaveBeenCalledWith(10));
		await expect.element(getByText('該当する書籍はありません。')).toBeInTheDocument();
	});

	test('カテゴリを追加して一覧へ反映する', async () => {
		const { getByRole, getByText } = await render(<Admin />);
		await vi.waitFor(() => expect(mocks.fetchAdminCategories).toHaveBeenCalledTimes(1));
		await getByRole('textbox', { name: '新しいカテゴリ名' }).fill('科学');
		await getByRole('button', { name: 'カテゴリ追加' }).click();

		await vi.waitFor(() => expect(mocks.createAdminCategory).toHaveBeenCalledWith('科学'));
		await expect.element(getByText('科学')).toBeInTheDocument();
	});

	test('通常カテゴリを名称変更できる', async () => {
		const { getByRole, getByText } = await render(<Admin />);
		await vi.waitFor(() => expect(mocks.fetchAdminCategories).toHaveBeenCalledTimes(1));
		await getByRole('button', { name: '名称変更' }).click();
		await getByRole('textbox', { name: '技術書 の新しいカテゴリ名' }).fill('開発');
		await getByRole('button', { name: '保存', exact: true }).click();

		await vi.waitFor(() => expect(mocks.renameAdminCategory).toHaveBeenCalledWith(17, '開発'));
		await expect.element(getByText('開発')).toBeInTheDocument();
	});

	test('カテゴリ削除後にカテゴリ一覧と書籍一覧を再取得する', async () => {
		mocks.fetchAdminBooks.mockResolvedValueOnce([activeBook]).mockResolvedValueOnce([{ ...activeBook, categoryId: 1, category: { id: 1, name: '未分類' } }]);
		const { getByRole } = await render(<Admin />);
		await vi.waitFor(() => expect(mocks.fetchAdminBooks).toHaveBeenCalledWith('active'));
		await getByRole('button', { name: 'カテゴリ削除' }).click();

		await vi.waitFor(() => expect(mocks.deleteAdminCategory).toHaveBeenCalledWith(17));
		await vi.waitFor(() => expect(mocks.fetchAdminBooks).toHaveBeenCalledTimes(2));
		await expect.element(getByRole('cell', { name: '未分類', exact: true })).toBeInTheDocument();
	});
});
