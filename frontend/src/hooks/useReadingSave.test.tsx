import { afterEach, describe, expect, test, vi } from 'vitest';
import { cleanup, renderHook } from 'vitest-browser-react';

const mocks = vi.hoisted(() => ({
	registerReadingFlush: vi.fn(() => () => {}),
	saveReadingInfo: vi.fn(),
}));

vi.mock('@/api/readingInfo', () => ({
	registerReadingFlush: mocks.registerReadingFlush,
	saveReadingInfo: mocks.saveReadingInfo,
}));

import { useReadingSave } from './useReadingSave';

afterEach(async () => {
	await cleanup();
	vi.clearAllMocks();
});

describe('useReadingSave initial status', () => {
	test('keeps a fresh page-one unread record unconfirmed instead of claiming it was saved', async () => {
		const hook = await renderHook(() => useReadingSave(7));
		expect(hook.result.current.status).toBe('unknown');

		await hook.act(async () => {
			hook.result.current.confirmInitialStatus({
				bookId: 7,
				currentPosition: null,
				currentPage: 1,
				readStatus: 'unread',
			});
		});

		expect(hook.result.current.status).toBe('unknown');
	});

	test('shows saved only when the server reports an existing reading position', async () => {
		const hook = await renderHook(() => useReadingSave(7));

		await hook.act(async () => {
			hook.result.current.confirmInitialStatus({
				bookId: 7,
				currentPosition: 'epubcfi(/6/2!/4/1:0)',
				currentPage: 1,
				readStatus: 'reading',
			});
		});

		expect(hook.result.current.status).toBe('saved');
	});
});
