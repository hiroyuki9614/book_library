import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { renderHook } from 'vitest-browser-react';

const mocks = vi.hoisted(() => ({
	registerReadingFlush: vi.fn(() => () => {}),
	saveReadingInfo: vi.fn(),
	getReadingSaveGeneration: vi.fn(() => 11),
	reserveReadingSave: vi.fn(() => Promise.resolve('23')),
}));

vi.mock('@/api/readingInfo', () => ({
	registerReadingFlush: mocks.registerReadingFlush,
	saveReadingInfo: mocks.saveReadingInfo,
	getReadingSaveGeneration: mocks.getReadingSaveGeneration,
	reserveReadingSave: mocks.reserveReadingSave,
}));

import { useReadingSave } from './useReadingSave';

function deferred<T>() {
	let resolve!: (value: T | PromiseLike<T>) => void;
	let reject!: (reason?: unknown) => void;
	const promise = new Promise<T>((resolvePromise, rejectPromise) => {
		resolve = resolvePromise;
		reject = rejectPromise;
	});
	return { promise, resolve, reject };
}

beforeEach(() => {
	mocks.registerReadingFlush.mockReset();
	mocks.registerReadingFlush.mockImplementation(() => () => {});
	mocks.saveReadingInfo.mockReset();
	mocks.saveReadingInfo.mockResolvedValue({});
	mocks.getReadingSaveGeneration.mockReset();
	mocks.getReadingSaveGeneration.mockReturnValue(11);
	mocks.reserveReadingSave.mockReset();
	mocks.reserveReadingSave.mockResolvedValue('23');
});

afterEach(() => {
	vi.clearAllMocks();
});

describe('useReadingSave', () => {
	test('キュー投入時に保存シーケンス予約をすぐ開始する', async () => {
		const { result, unmount, act } = await renderHook(({ userId }: { userId?: number } = {}) => useReadingSave(99, userId), {
			initialProps: { userId: 7 },
		});

		await act(async () => {
			result.current.save(2, 'reading', 20);
		});

		expect(mocks.reserveReadingSave).toHaveBeenCalledTimes(1);
		expect(mocks.reserveReadingSave).toHaveBeenCalledWith(99, 2, 'reading', 20, 7, 11);
		expect(mocks.saveReadingInfo).not.toHaveBeenCalled();
		unmount();
	});

	test.each(['pagehide', 'visibilitychange', 'unmount'] as const)('%s は予約完了を待たず keepalive 保存を開始する', async (lifecycle) => {
		const reservation = deferred<string>();
		mocks.reserveReadingSave.mockReturnValue(reservation.promise);
		const { result, unmount, act } = await renderHook(() => useReadingSave(99, 7));

		await act(async () => {
			result.current.save(2, 'reading', 20);
		});
		if (lifecycle === 'pagehide') {
			let pagehideReturned = false;
			mocks.saveReadingInfo.mockImplementation(() => {
				expect(pagehideReturned).toBe(false);
				return Promise.resolve({});
			});
			await act(() => {
				window.dispatchEvent(new Event('pagehide'));
				expect(mocks.saveReadingInfo).toHaveBeenCalledTimes(1);
				pagehideReturned = true;
			});
		} else if (lifecycle === 'visibilitychange') {
			const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);
			await act(() => {
				document.dispatchEvent(new Event('visibilitychange'));
				expect(mocks.saveReadingInfo).toHaveBeenCalledTimes(1);
			});
			hidden.mockRestore();
		} else {
			const unmountPromise = unmount();
			expect(mocks.saveReadingInfo).toHaveBeenCalledTimes(1);
			await unmountPromise;
		}

		expect(mocks.saveReadingInfo).toHaveBeenCalledTimes(1);
		expect(mocks.saveReadingInfo).toHaveBeenCalledWith(99, 2, 'reading', 20, 7, 11, true, undefined);
		reservation.resolve('23');
		await act(async () => {
			await Promise.resolve();
		});
		if (lifecycle !== 'unmount') unmount();
	});

	test('予約失敗後のretryは新しい予約を開始し、保存要求を再試行する', async () => {
		const firstReservation = deferred<string>();
		const secondReservation = deferred<string>();
		mocks.reserveReadingSave
			.mockReturnValueOnce(firstReservation.promise)
			.mockReturnValueOnce(secondReservation.promise);
		const { result, unmount, act } = await renderHook(() => useReadingSave(99, 7));

		await act(async () => {
			result.current.save(2);
		});
		await act(async () => {
			firstReservation.reject(new Error('auth generation invalidated'));
			await Promise.resolve();
		});
		await act(async () => {
			result.current.retry();
		});

		expect(mocks.reserveReadingSave).toHaveBeenCalledTimes(2);
		expect(mocks.saveReadingInfo).toHaveBeenCalledTimes(1);
		expect(mocks.saveReadingInfo).toHaveBeenCalledWith(99, 2, 'reading', undefined, 7, 11, false, undefined);
		secondReservation.resolve('24');
		await act(async () => {
			await Promise.resolve();
		});
		unmount();
	});

	test('保存要求の失敗後retryは同じ予約シーケンスで再試行する', async () => {
		mocks.saveReadingInfo.mockRejectedValueOnce(new Error('network failure'));
		const { result, unmount, act } = await renderHook(() => useReadingSave(99, 7));

		await act(async () => {
			result.current.save(2);
			await Promise.resolve();
			window.dispatchEvent(new Event('pagehide'));
		});
		expect(result.current.status).toBe('error');

		await act(async () => {
			result.current.retry();
		});
		expect(mocks.saveReadingInfo).toHaveBeenCalledTimes(2);
		expect(mocks.saveReadingInfo).toHaveBeenLastCalledWith(99, 2, 'reading', undefined, 7, 11, false, '23');
		expect(result.current.status).toBe('saved');
		unmount();
	});

	test('409 reconciliation後の保存要求失敗をretryすると更新済みsequenceを使う', async () => {
		mocks.saveReadingInfo.mockRejectedValueOnce(Object.assign(
			new Error('network failure after sequence reconciliation'),
			{ saveSequence: '51' },
		));
		const { result, unmount, act } = await renderHook(() => useReadingSave(99, 7));

		await act(async () => {
			result.current.save(2);
			await Promise.resolve();
			window.dispatchEvent(new Event('pagehide'));
		});
		expect(result.current.status).toBe('error');

		await act(async () => {
			result.current.retry();
		});

		expect(mocks.saveReadingInfo).toHaveBeenCalledTimes(2);
		expect(mocks.saveReadingInfo).toHaveBeenLastCalledWith(99, 2, 'reading', undefined, 7, 11, false, '51');
		expect(result.current.status).toBe('saved');
		unmount();
	});

	test('pagehide中の遅延予約をretryのシーケンスとして再利用しない', async () => {
		const oldReservation = deferred<string>();
		const secondReservation = deferred<string>();
		mocks.reserveReadingSave.mockReturnValueOnce(oldReservation.promise).mockReturnValueOnce(secondReservation.promise);
		mocks.saveReadingInfo.mockRejectedValueOnce(new Error('network failure'));
		const { result, rerender, unmount, act } = await renderHook(({ userId }: { userId: number } = { userId: 7 }) => useReadingSave(99, userId), {
			initialProps: { userId: 7 },
		});

		await act(async () => {
			result.current.save(2);
			window.dispatchEvent(new Event('pagehide'));
		});
		await act(async () => {
			oldReservation.resolve('23');
			await Promise.resolve();
		});

		await act(async () => {
			result.current.retry();
		});
		expect(mocks.reserveReadingSave).toHaveBeenCalledTimes(2);
		expect(mocks.saveReadingInfo).toHaveBeenCalledTimes(2);
		expect(mocks.saveReadingInfo).toHaveBeenLastCalledWith(99, 2, 'reading', undefined, 7, 11, false, undefined);
		expect(mocks.saveReadingInfo).not.toHaveBeenCalledWith(99, 2, 'reading', undefined, 7, 11, false, '23');
		secondReservation.resolve('24');
		await act(async () => {
			await Promise.resolve();
		});
		expect(mocks.saveReadingInfo).toHaveBeenLastCalledWith(99, 2, 'reading', undefined, 7, 11, false, undefined);

		await act(async () => {
			rerender({ userId: 8 });
		});
		await act(async () => {
			result.current.save(3);
			window.dispatchEvent(new Event('pagehide'));
		});
		expect(mocks.saveReadingInfo).toHaveBeenLastCalledWith(99, 3, 'reading', undefined, 8, 11, true, undefined);
		unmount();
	});

	test('アカウント切替後に保持された旧save callbackは予約・保存を行わない', async () => {
		const { result, rerender, unmount, act } = await renderHook(({ userId }: { userId: number } = { userId: 7 }) => useReadingSave(99, userId), {
			initialProps: { userId: 7 },
		});
		const staleSave = result.current.save;

		await act(async () => {
			rerender({ userId: 8 });
		});
		await act(() => {
			staleSave(2);
			expect(mocks.reserveReadingSave).not.toHaveBeenCalled();
			window.dispatchEvent(new Event('pagehide'));
			expect(mocks.saveReadingInfo).not.toHaveBeenCalled();
		});

		await act(async () => {
			result.current.save(3);
		});
		expect(mocks.reserveReadingSave).toHaveBeenCalledTimes(1);
		expect(mocks.reserveReadingSave).toHaveBeenCalledWith(99, 3, 'reading', undefined, 8, 11);
		await act(() => {
			window.dispatchEvent(new Event('pagehide'));
			expect(mocks.saveReadingInfo).toHaveBeenCalledTimes(1);
		});
		expect(mocks.saveReadingInfo).toHaveBeenCalledWith(99, 3, 'reading', undefined, 8, 11, true, '23');
		expect(mocks.saveReadingInfo).not.toHaveBeenCalledWith(99, 2, 'reading', undefined, 8, 11, true, expect.anything());

		unmount();
	});

	test('予約拒否を処理し、認証世代無効化時も未処理rejectionを残さない', async () => {
		const reservation = deferred<string>();
		mocks.reserveReadingSave.mockReturnValue(reservation.promise);
		const { result, unmount, act } = await renderHook(() => useReadingSave(99, 7));

		await act(async () => {
			result.current.save(2);
			reservation.reject(new Error('reading save invalidated by authentication change'));
			await Promise.resolve();
		});
		await act(async () => {
			window.dispatchEvent(new Event('pagehide'));
		});

		expect(mocks.saveReadingInfo).toHaveBeenCalledWith(99, 2, 'reading', undefined, 7, 11, true, undefined);
		expect(result.current.status).toBe('saved');
		unmount();
	});

	test('アカウント切替後に旧アカウントの保存を送信しない', async () => {
		const oldReservation = deferred<string>();
		mocks.reserveReadingSave.mockReturnValueOnce(oldReservation.promise).mockResolvedValueOnce('24');
		const { result, rerender, unmount, act } = await renderHook(({ userId }: { userId: number } = { userId: 7 }) => useReadingSave(99, userId), {
			initialProps: { userId: 7 },
		});

		await act(async () => {
			result.current.save(2);
			rerender({ userId: 8 });
		});
		oldReservation.resolve('23');
		await act(async () => {
			await Promise.resolve();
		});
		expect(mocks.saveReadingInfo).not.toHaveBeenCalled();

		await act(async () => {
			result.current.save(3);
			await Promise.resolve();
		});
		await act(async () => {
			window.dispatchEvent(new Event('pagehide'));
		});
		expect(mocks.saveReadingInfo).toHaveBeenCalledTimes(1);
		expect(mocks.saveReadingInfo).toHaveBeenCalledWith(99, 3, 'reading', undefined, 8, 11, true, '24');
		expect(mocks.saveReadingInfo).not.toHaveBeenCalledWith(99, 3, 'reading', undefined, 8, 11, true, '23');

		unmount();
	});
	test('keeps a fresh page-one unread record unconfirmed instead of claiming it was saved', async () => {
		const hook = await renderHook(() => useReadingSave(7));
		expect(hook.result.current.status).toBe('unknown');

		await hook.act(async () => {
			hook.result.current.confirmInitialStatus({
				bookId: 7,
				currentPosition: null,
				currentPage: 1,
				progressPercentage: 0,
				readStatus: 'unread',
			});
		});

		expect(hook.result.current.status).toBe('unknown');
		hook.unmount();
	});

	test('shows saved only when the server reports an existing reading position', async () => {
		const hook = await renderHook(() => useReadingSave(7));

		await hook.act(async () => {
			hook.result.current.confirmInitialStatus({
				bookId: 7,
				currentPosition: 'epubcfi(/6/2!/4/1:0)',
				currentPage: 1,
				progressPercentage: 0,
				readStatus: 'reading',
			});
		});

		expect(hook.result.current.status).toBe('saved');
		hook.unmount();
	});

	test('initial save conflict is distinct from a retryable save failure', async () => {
		const hook = await renderHook(() => useReadingSave(7, 11));

		await hook.act(async () => {
			hook.result.current.confirmInitialStatus({
				bookId: 7,
				currentPosition: 'epubcfi(/6/2!/4/1:0)',
				currentPage: 9,
				progressPercentage: 40,
				readStatus: 'reading',
				saveConflict: true,
			});
		});

		expect(hook.result.current.status).toBe('conflict');
		await hook.act(async () => {
			hook.result.current.retry();
		});
		expect(mocks.reserveReadingSave).not.toHaveBeenCalled();
		expect(mocks.saveReadingInfo).not.toHaveBeenCalled();
		hook.unmount();
	});

});
