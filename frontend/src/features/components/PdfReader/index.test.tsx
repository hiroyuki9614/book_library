import { beforeEach, describe, expect, test, vi } from 'vitest';
import { render } from 'vitest-browser-react';
import { PageNavigation } from './index';

const mocks = vi.hoisted(() => ({
	useScroll: vi.fn(),
	useCommand: vi.fn(),
	saveReadingInfo: vi.fn(),
}));

vi.mock('@embedpdf/plugin-scroll/react', async () => {
	const actual = await vi.importActual<typeof import('@embedpdf/plugin-scroll/react')>('@embedpdf/plugin-scroll/react');
	return { ...actual, useScroll: mocks.useScroll };
});

vi.mock('@embedpdf/plugin-commands/react', async () => {
	const actual = await vi.importActual<typeof import('@embedpdf/plugin-commands/react')>('@embedpdf/plugin-commands/react');
	return { ...actual, useCommand: mocks.useCommand };
});

vi.mock('@/api/readingInfo', () => ({ fetchReadingInfo: vi.fn(), saveReadingInfo: mocks.saveReadingInfo }));
vi.mock('./Command', () => ({ default: () => <div /> }));

beforeEach(() => {
	vi.resetAllMocks();
	mocks.useScroll.mockReturnValue({
		provides: { scrollToPage: vi.fn() },
		state: { currentPage: 1, totalPages: 1 },
	});
	mocks.useCommand.mockReturnValue({ visible: true, disabled: false, execute: vi.fn(), label: 'command' });
	mocks.saveReadingInfo.mockResolvedValue({ bookId: 7, currentPage: 1, readStatus: 'completed' });
});

describe('PageNavigation persistence guard', () => {
	test('未読の1ページPDFは初期表示時にcompleted保存する', async () => {
		await render(<PageNavigation bookId={7} documentId='document-1' initialPage={1} initialReadStatus='unread' />);

		expect(mocks.saveReadingInfo).toHaveBeenCalledWith(7, 1, 1);
	});

	test('既存のcompleted位置のrestoreでは初期保存しない', async () => {
		await render(<PageNavigation bookId={7} documentId='document-1' initialPage={1} initialReadStatus='completed' />);

		expect(mocks.saveReadingInfo).not.toHaveBeenCalled();
	});

	test('reading状態で最終ページをrestoreした場合もcompleted保存する', async () => {
		mocks.useScroll.mockReturnValue({
			provides: { scrollToPage: vi.fn() },
			state: { currentPage: 10, totalPages: 10 },
		});
		mocks.saveReadingInfo.mockResolvedValue({ bookId: 7, currentPage: 10, readStatus: 'completed' });

		await render(<PageNavigation bookId={7} documentId='document-1' initialPage={10} initialReadStatus='reading' />);

		expect(mocks.saveReadingInfo).toHaveBeenCalledWith(7, 10, 10);
	});
});
