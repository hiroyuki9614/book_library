import { describe, expect, test, vi } from 'vitest';
import { persistReadingInfo, toReadingProgress } from './readingProgress.js';

function createPersistence() {
	return {
		readingInfo: {
			findUnique: vi.fn(),
			updateMany: vi.fn(),
			create: vi.fn(),
			update: vi.fn(),
			upsert: vi.fn(),
		},
	} as never as {
		readingInfo: Record<string, ReturnType<typeof vi.fn>>;
	};
}

describe('persistReadingInfo', () => {
	test('normalizes unread, completed, and unmeasured legacy states', () => {
		expect(toReadingProgress(null)).toBe(0);
		expect(toReadingProgress({ readStatus: 'completed', progressPercentage: 12 })).toBe(100);
		expect(toReadingProgress({ readStatus: 'reading', progressPercentage: 42 })).toBe(42);
		expect(toReadingProgress({ readStatus: 'reading', progressPercentage: 101 })).toBeNull();
		expect(toReadingProgress({ readStatus: 'reading', progressPercentage: null })).toBeNull();
	});

	test('saves a reading position on a completed row without downgrading its status', async () => {
		const persistence = createPersistence();
		persistence.readingInfo.updateMany
			.mockResolvedValueOnce({ count: 0 })
			.mockResolvedValueOnce({ count: 1 });
		persistence.readingInfo.findUnique
			.mockResolvedValueOnce({ currentPosition: '12', progressPercentage: 100, readStatus: 'completed' })
			.mockResolvedValueOnce({ currentPosition: '3', progressPercentage: 100, readStatus: 'completed' });

		await expect(persistReadingInfo(persistence as never, {
			userId: 7,
			bookId: 1,
			currentPosition: '3',
			readStatus: 'reading',
			progressPercentage: 25,
		})).resolves.toEqual({ currentPosition: '3', progressPercentage: 100, readStatus: 'completed' });

		expect(persistence.readingInfo.updateMany).toHaveBeenNthCalledWith(1, {
			where: { userId_bookId: { userId: 7, bookId: 1 }, readStatus: { not: 'completed' } },
			data: { currentPosition: '3', readStatus: 'reading', progressPercentage: 25 },
		});
		expect(persistence.readingInfo.updateMany).toHaveBeenNthCalledWith(2, {
			where: { userId_bookId: { userId: 7, bookId: 1 }, readStatus: 'completed' },
			data: { currentPosition: '3', readStatus: 'completed', progressPercentage: 100 },
		});
		expect(persistence.readingInfo.updateMany).toHaveBeenCalledTimes(2);
		expect(persistence.readingInfo.update).not.toHaveBeenCalled();
	});

	test('retries a unique-key race when the first reading save creates the row', async () => {
		const persistence = createPersistence();
		const saved = { currentPosition: '9', progressPercentage: 50, readStatus: 'reading' };
		persistence.readingInfo.updateMany
			.mockResolvedValueOnce({ count: 0 })
			.mockResolvedValueOnce({ count: 1 });
		persistence.readingInfo.findUnique
			.mockResolvedValueOnce(null)
			.mockResolvedValueOnce(saved);
		persistence.readingInfo.create.mockRejectedValue(Object.assign(new Error('duplicate'), { code: 'P2002' }));

		await expect(persistReadingInfo(persistence as never, {
			userId: 7,
			bookId: 1,
			currentPosition: '9',
			readStatus: 'reading',
			progressPercentage: 50,
		})).resolves.toEqual(saved);

		expect(persistence.readingInfo.updateMany).toHaveBeenCalledTimes(2);
		expect(persistence.readingInfo.create).toHaveBeenCalledTimes(1);
	});
});
