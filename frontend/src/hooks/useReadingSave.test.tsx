import { afterEach, describe, expect, test, vi } from 'vitest';
import { renderHook } from 'vitest-browser-react';

const mocks = vi.hoisted(() => ({
	registerReadingFlush: vi.fn(() => () => {}),
	saveReadingInfo: vi.fn(),
}));

vi.mock('@/api/readingInfo', () => ({
	registerReadingFlush: mocks.registerReadingFlush,
	saveReadingInfo: mocks.saveReadingInfo,
}));

import { useReadingSave } from './useReadingSave';

afterEach(() => {
	vi.clearAllMocks();
});

describe('useReadingSave', () => {
	test('アカウント切替時に旧アカウントの最新位置を新アカウントのretryへ引き継がない', async () => {
		mocks.saveReadingInfo.mockResolvedValue({});
		const { result, rerender, act } = await renderHook(({ userId }: { userId?: number } = {}) => useReadingSave(99, userId), {
			initialProps: { userId: 7 },
		});

		await act(async () => {
			result.current.save(2, 'reading', 20);
		});
		await act(async () => {
			rerender({ userId: 8 });
		});

		expect(mocks.saveReadingInfo).toHaveBeenCalledTimes(1);
		expect(mocks.saveReadingInfo).toHaveBeenCalledWith(99, 2, 'reading', 20, 7);

		await act(async () => {
			result.current.retry();
		});
		expect(mocks.saveReadingInfo).toHaveBeenCalledTimes(1);
	});
});
