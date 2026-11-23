import { describe, expect, test, vi } from 'vitest';
import {
	MAX_CURRENT_POSITION_LENGTH,
	persistReadingInfo,
	StaleReadingInfoError,
	toReadingProgress,
} from './readingProgress.js';

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
	test('rejects an oversized current position before reading or writing', async () => {
		const prisma = createPersistence();

		for (const readStatus of ['reading', 'completed'] as const) {
			await expect(persistReadingInfo(prisma, {
				userId: 7,
				bookId: 1,
				currentPosition: 'x'.repeat(MAX_CURRENT_POSITION_LENGTH + 1),
				readStatus,
				progressPercentage: undefined,
			})).rejects.toThrow(`currentPosition must be ${MAX_CURRENT_POSITION_LENGTH} characters or fewer`);

			expect(prisma.readingInfo.findUnique).not.toHaveBeenCalled();
			expect(prisma.readingInfo.updateMany).not.toHaveBeenCalled();
			expect(prisma.readingInfo.create).not.toHaveBeenCalled();
			expect(prisma.readingInfo.update).not.toHaveBeenCalled();
			expect(prisma.readingInfo.upsert).not.toHaveBeenCalled();
		}
	});

	test('persists and returns a long EPUB CFI through the persistence seam', async () => {
		const cfi = `epubcfi(/6/${'4/'.repeat(159)}4:0)`;
		const prisma = createPersistence();
		prisma.readingInfo.updateMany.mockResolvedValue({ count: 0 });
		prisma.readingInfo.findUnique.mockResolvedValue(null);
		prisma.readingInfo.create.mockImplementation(({ data }) => data);

		const saved = await persistReadingInfo(prisma, {
			userId: 7,
			bookId: 1,
			currentPosition: cfi,
			readStatus: 'reading',
			progressPercentage: undefined,
		});

		expect(cfi).toHaveLength(333);
		expect(prisma.readingInfo.create).toHaveBeenCalledWith({
			data: { userId: 7, bookId: 1, currentPosition: cfi, readStatus: 'reading', saveSequence: 0n },
		});
		expect(saved.currentPosition).toBe(cfi);
	});

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
			where: { userId: 7, bookId: 1, readStatus: { not: 'completed' }, saveSequence: { equals: 0n } },
			data: { currentPosition: '3', readStatus: 'reading', progressPercentage: 25, saveSequence: 0n },
		});
		expect(persistence.readingInfo.updateMany).toHaveBeenNthCalledWith(2, {
			where: { userId: 7, bookId: 1, readStatus: 'completed', saveSequence: { equals: 0n } },
			data: { currentPosition: '3', readStatus: 'completed', progressPercentage: 100, saveSequence: 0n },
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

	test('rejects a maximum sequence on an empty record without creating a poisoned row', async () => {
		const persistence = createPersistence();
		persistence.readingInfo.updateMany.mockResolvedValue({ count: 0 });
		persistence.readingInfo.findUnique.mockResolvedValue(null);

		await expect(persistReadingInfo(persistence as never, {
			userId: 7,
			bookId: 1,
			currentPosition: '1',
			readStatus: 'reading',
			progressPercentage: 1,
			saveSequence: 9223372036854775807n,
			expectedSaveSequence: 0n,
		})).rejects.toBeInstanceOf(StaleReadingInfoError);

		expect(persistence.readingInfo.create).not.toHaveBeenCalled();
	});

	test('rejects a maximum sequence against a low-sequence row without changing it', async () => {
		const persistence = createPersistence();
		persistence.readingInfo.updateMany.mockResolvedValue({ count: 0 });
		persistence.readingInfo.findUnique.mockResolvedValue({
			currentPosition: '1',
			progressPercentage: 1,
			readStatus: 'reading',
			saveSequence: 0n,
		});

		await expect(persistReadingInfo(persistence as never, {
			userId: 7,
			bookId: 1,
			currentPosition: '2',
			readStatus: 'reading',
			progressPercentage: 2,
			saveSequence: 9223372036854775807n,
			expectedSaveSequence: 0n,
		})).rejects.toBeInstanceOf(StaleReadingInfoError);

		expect(persistence.readingInfo.create).not.toHaveBeenCalled();
	});

	test('accepts incremental versions and rejects a concurrent stale retry', async () => {
		const persistence = createPersistence();
		const firstSaved = { currentPosition: '1', progressPercentage: 1, readStatus: 'reading', saveSequence: 1n };
		const secondSaved = { currentPosition: '2', progressPercentage: 2, readStatus: 'reading', saveSequence: 2n };
		persistence.readingInfo.updateMany
			.mockResolvedValueOnce({ count: 0 })
			.mockResolvedValueOnce({ count: 1 })
			.mockResolvedValueOnce({ count: 0 });
		persistence.readingInfo.findUnique
			.mockResolvedValueOnce(null)
			.mockResolvedValueOnce(secondSaved)
			.mockResolvedValueOnce(secondSaved);
		persistence.readingInfo.create.mockResolvedValue(firstSaved);

		await expect(persistReadingInfo(persistence as never, {
			userId: 7,
			bookId: 1,
			currentPosition: '1',
			readStatus: 'reading',
			progressPercentage: 1,
			saveSequence: 1n,
			expectedSaveSequence: 0n,
		})).resolves.toEqual(firstSaved);

		await expect(persistReadingInfo(persistence as never, {
			userId: 7,
			bookId: 1,
			currentPosition: '2',
			readStatus: 'reading',
			progressPercentage: 2,
			saveSequence: 2n,
			expectedSaveSequence: 1n,
		})).resolves.toEqual(secondSaved);

		await expect(persistReadingInfo(persistence as never, {
			userId: 7,
			bookId: 1,
			currentPosition: '2',
			readStatus: 'reading',
			progressPercentage: 2,
			saveSequence: 2n,
			expectedSaveSequence: 0n,
		})).rejects.toBeInstanceOf(StaleReadingInfoError);

		expect(persistence.readingInfo.updateMany).toHaveBeenNthCalledWith(1, expect.objectContaining({
			where: expect.objectContaining({ saveSequence: { equals: 0n } }),
		}));
		expect(persistence.readingInfo.updateMany).toHaveBeenNthCalledWith(2, expect.objectContaining({
			where: expect.objectContaining({ saveSequence: { equals: 1n } }),
		}));
		expect(persistence.readingInfo.updateMany).toHaveBeenNthCalledWith(3, expect.objectContaining({
			where: expect.objectContaining({ saveSequence: { equals: 0n } }),
		}));
	});

	test('allows a newer reserved version to advance a sequence-zero row', async () => {
		const persistence = createPersistence();
		const saved = { currentPosition: '9', progressPercentage: 80, readStatus: 'reading', saveSequence: 2n };
		persistence.readingInfo.updateMany.mockResolvedValue({ count: 1 });
		persistence.readingInfo.findUnique.mockResolvedValue(saved);

		await expect(persistReadingInfo(persistence as never, {
			userId: 7,
			bookId: 1,
			currentPosition: '9',
			readStatus: 'reading',
			progressPercentage: 80,
			saveSequence: 2n,
			expectedSaveSequence: 0n,
		})).resolves.toEqual(saved);

		expect(persistence.readingInfo.updateMany).toHaveBeenCalledWith({
			where: {
				userId: 7,
				bookId: 1,
				readStatus: { not: 'completed' },
				saveSequence: { equals: 0n },
			},
			data: { currentPosition: '9', readStatus: 'reading', progressPercentage: 80, saveSequence: 2n },
		});
	});

	test('rejects a high local sequence when another device advanced the server row past the expected base', async () => {
		const persistence = createPersistence();
		persistence.readingInfo.updateMany.mockResolvedValue({ count: 0 });
		persistence.readingInfo.findUnique.mockResolvedValue({
			currentPosition: '9',
			progressPercentage: 90,
			readStatus: 'reading',
			saveSequence: 1n,
		});

		await expect(persistReadingInfo(persistence as never, {
			userId: 7,
			bookId: 1,
			currentPosition: '5',
			readStatus: 'reading',
			progressPercentage: 50,
			saveSequence: 101n,
			expectedSaveSequence: 0n,
		})).rejects.toBeInstanceOf(StaleReadingInfoError);

		expect(persistence.readingInfo.updateMany).toHaveBeenCalledWith(expect.objectContaining({
			where: expect.objectContaining({ saveSequence: { equals: 0n } }),
		}));
		expect(persistence.readingInfo.create).not.toHaveBeenCalled();
	});
});
