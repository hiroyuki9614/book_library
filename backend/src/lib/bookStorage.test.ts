import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
	send: vi.fn(),
	destroy: vi.fn(),
	getSignedUrl: vi.fn(),
}));

vi.mock('@aws-sdk/client-s3', async (importOriginal) => {
	const actual = await importOriginal<typeof import('@aws-sdk/client-s3')>();
	return {
		...actual,
		S3Client: class {
			send = mocks.send;
			destroy = mocks.destroy;
		},
	};
});

vi.mock('@aws-sdk/s3-request-presigner', () => ({
	getSignedUrl: mocks.getSignedUrl,
}));

import { DeleteObjectCommand, HeadObjectCommand, PutObjectCommand } from '@aws-sdk/client-s3';
import {
	BOOK_FILE_SIGNED_URL_EXPIRES_IN_SECONDS,
	deleteStoredBookFile,
	getBookFileAccess,
	storeBookFile,
} from './bookStorage.js';

const envBackup = { ...process.env };

describe('R2 book storage', () => {
	beforeEach(() => {
		process.env.BOOK_FILE_STORAGE_DRIVER = 'r2';
		process.env.R2_ACCOUNT_ID = 'account-id';
		process.env.R2_ACCESS_KEY_ID = 'access-key';
		process.env.R2_SECRET_ACCESS_KEY = 'secret-key';
		process.env.R2_BUCKET_NAME = 'belib-books';
		delete process.env.R2_ENDPOINT;
		mocks.send.mockReset();
		mocks.destroy.mockReset();
		mocks.getSignedUrl.mockReset();
	});

	afterEach(() => {
		process.env = { ...envBackup };
	});

	test('uploads a protected book object under the books prefix', async () => {
		mocks.send.mockResolvedValueOnce({});

		const stored = await storeBookFile({
			fileType: 'pdf',
			bytes: Buffer.from('%PDF-1.7'),
			mimeType: 'application/pdf',
			originalFileName: 'sample.pdf',
		});

		expect(stored.fileUrl).toMatch(/^books\/[\da-f-]+\.pdf$/);
		expect(stored.storedFileName).toMatch(/^[\da-f-]+\.pdf$/);
		const command = mocks.send.mock.calls[0]?.[0];
		expect(command).toBeInstanceOf(PutObjectCommand);
		expect(command.input).toMatchObject({
			Bucket: 'belib-books',
			Key: stored.fileUrl,
			ContentType: 'application/pdf',
			CacheControl: 'private, no-store',
		});
	});

	test('checks object existence and issues a one-hour signed URL', async () => {
		mocks.send.mockResolvedValueOnce({});
		mocks.getSignedUrl.mockResolvedValueOnce('https://signed.example/object');

		const before = Date.now();
		const access = await getBookFileAccess('books/example.pdf');
		const after = Date.now();

		expect(mocks.send.mock.calls[0]?.[0]).toBeInstanceOf(HeadObjectCommand);
		expect(mocks.getSignedUrl).toHaveBeenCalledWith(
			expect.anything(),
			expect.anything(),
			{ expiresIn: BOOK_FILE_SIGNED_URL_EXPIRES_IN_SECONDS },
		);
		expect(access.kind).toBe('redirect');
		if (access.kind === 'redirect') {
			expect(access.url).toBe('https://signed.example/object');
			const expiresAt = Date.parse(access.expiresAt);
			expect(expiresAt).toBeGreaterThanOrEqual(before + BOOK_FILE_SIGNED_URL_EXPIRES_IN_SECONDS * 1000);
			expect(expiresAt).toBeLessThanOrEqual(after + BOOK_FILE_SIGNED_URL_EXPIRES_IN_SECONDS * 1000);
		}
	});

	test('deletes the R2 object during compensation cleanup', async () => {
		mocks.send.mockResolvedValueOnce({});

		await deleteStoredBookFile('books/example.epub');

		const command = mocks.send.mock.calls[0]?.[0];
		expect(command).toBeInstanceOf(DeleteObjectCommand);
		expect(command.input).toEqual({ Bucket: 'belib-books', Key: 'books/example.epub' });
	});
});
