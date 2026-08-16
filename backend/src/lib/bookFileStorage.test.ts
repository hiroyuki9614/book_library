import { mkdtemp, readFile, readdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { createBookFileStorage } from './bookFileStorage.js';

const mocks = vi.hoisted(() => ({ send: vi.fn() }));

vi.mock('@aws-sdk/client-s3', () => ({
	S3Client: class S3Client {
		send = mocks.send;
	},
	PutObjectCommand: class PutObjectCommand {
		constructor(public input: unknown) {}
	},
	GetObjectCommand: class GetObjectCommand {
		constructor(public input: unknown) {}
	},
	DeleteObjectCommand: class DeleteObjectCommand {
		constructor(public input: unknown) {}
	},
}));

let storageRoot: string | undefined;

afterEach(async () => {
	if (storageRoot) {
		await rm(storageRoot, { recursive: true, force: true });
		storageRoot = undefined;
	}
	vi.resetAllMocks();
});

describe('book file storage adapters', () => {
	test('local adapter stores and retrieves a private relative key', async () => {
		storageRoot = await mkdtemp(join(tmpdir(), 'belib-storage-'));
		const storage = createBookFileStorage({ BOOK_FILE_STORAGE_ROOT: storageRoot });

		await storage.put('books/1/file.pdf', Buffer.from('%PDF-1.7'), 'application/pdf');
		const body = await new Promise<Buffer>((resolve, reject) => {
			const chunks: Buffer[] = [];
			storage.get('books/1/file.pdf').then((stream) => {
				stream.on('data', (chunk) => chunks.push(Buffer.from(chunk)));
				stream.on('end', () => resolve(Buffer.concat(chunks)));
				stream.on('error', reject);
			});
		});

		expect(body.toString()).toBe('%PDF-1.7');
		expect(await readFile(join(storageRoot, 'books/1/file.pdf'))).toEqual(Buffer.from('%PDF-1.7'));
		await storage.delete('books/1/file.pdf');
		expect(await readdir(join(storageRoot, 'books/1'))).toEqual([]);
	});

	test('local adapter rejects traversal keys', async () => {
		storageRoot = await mkdtemp(join(tmpdir(), 'belib-storage-'));
		const storage = createBookFileStorage({ BOOK_FILE_STORAGE_ROOT: storageRoot });

		await expect(storage.get('../outside.pdf')).rejects.toThrow('relative storage keys');
	});

	test('R2 adapter requires all server-side configuration', () => {
		expect(() => createBookFileStorage({ BOOK_FILE_STORAGE_DRIVER: 'r2' })).toThrow(
			'R2 storage requires R2_ENDPOINT, R2_BUCKET_NAME, R2_ACCESS_KEY_ID, and R2_SECRET_ACCESS_KEY',
		);
	});

	test('R2 adapter sends put/get/delete commands without exposing a public URL', async () => {
		const { Readable } = await import('node:stream');
		mocks.send.mockResolvedValueOnce({}).mockResolvedValueOnce({ Body: Readable.from([Buffer.from('%PDF-1.7')]) }).mockResolvedValueOnce({});
		const storage = createBookFileStorage({
			BOOK_FILE_STORAGE_DRIVER: 'r2',
			R2_ENDPOINT: 'https://account.r2.cloudflarestorage.com',
			R2_BUCKET_NAME: 'private-books',
			R2_ACCESS_KEY_ID: 'observed-test-key',
			R2_SECRET_ACCESS_KEY: 'observed-test-secret',
		});

		await storage.put('books/1/file.pdf', Buffer.from('%PDF-1.7'), 'application/pdf');
		const stream = await storage.get('books/1/file.pdf');
		await storage.delete('books/1/file.pdf');

		expect(stream).toBeInstanceOf(Readable);
		expect(mocks.send).toHaveBeenCalledTimes(3);
		expect(mocks.send.mock.calls[0][0].input).toMatchObject({ Bucket: 'private-books', Key: 'books/1/file.pdf' });
		expect(mocks.send.mock.calls[1][0].input).toMatchObject({ Bucket: 'private-books', Key: 'books/1/file.pdf' });
		expect(mocks.send.mock.calls[2][0].input).toMatchObject({ Bucket: 'private-books', Key: 'books/1/file.pdf' });
	});
});
