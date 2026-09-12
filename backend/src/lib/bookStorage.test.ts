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
	validateBookStorageConfig,
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
		const access = await getBookFileAccess('books/123e4567-e89b-12d3-a456-426614174000.pdf');
		const after = Date.now();

		expect(mocks.send.mock.calls[0]?.[0]).toBeInstanceOf(HeadObjectCommand);
		expect(mocks.getSignedUrl).toHaveBeenCalledWith(
			expect.anything(),
			expect.anything(),
			{ expiresIn: BOOK_FILE_SIGNED_URL_EXPIRES_IN_SECONDS },
		);
		expect(access.kind).toBe('signed-url');
		if (access.kind === 'signed-url') {
			expect(access.url).toBe('https://signed.example/object');
			const expiresAt = Date.parse(access.expiresAt);
			expect(expiresAt).toBeGreaterThanOrEqual(before + BOOK_FILE_SIGNED_URL_EXPIRES_IN_SECONDS * 1000);
			expect(expiresAt).toBeLessThanOrEqual(after + BOOK_FILE_SIGNED_URL_EXPIRES_IN_SECONDS * 1000);
		}
	});

	test('deletes the R2 object during compensation cleanup', async () => {
		mocks.send.mockResolvedValueOnce({});

		await deleteStoredBookFile('books/123e4567-e89b-12d3-a456-426614174000.epub');

		const command = mocks.send.mock.calls[0]?.[0];
		expect(command).toBeInstanceOf(DeleteObjectCommand);
		expect(command.input).toEqual({ Bucket: 'belib-books', Key: 'books/123e4567-e89b-12d3-a456-426614174000.epub' });
	});

	test('rejects non-UUID R2 keys before reading or deleting', async () => {
		await expect(getBookFileAccess('books/example.pdf')).rejects.toThrow('Book file not found');
		await expect(deleteStoredBookFile('books/123e4567-e89b-12d3-a456-426614174000.txt')).rejects.toThrow('Book file not found');
		expect(mocks.send).not.toHaveBeenCalled();
	});

	test('rejects incomplete R2 configuration during startup validation', () => {
		delete process.env.R2_SECRET_ACCESS_KEY;

		expect(() => validateBookStorageConfig()).toThrow('R2_SECRET_ACCESS_KEY is required');
	});

	test('attempts to delete the allocated key when an R2 upload fails', async () => {
		const uploadError = new Error('put failed');
		mocks.send.mockRejectedValueOnce(uploadError).mockResolvedValueOnce({});

		await expect(storeBookFile({
			fileType: 'pdf',
			bytes: Buffer.from('%PDF-1.7'),
			mimeType: 'application/pdf',
			originalFileName: 'sample.pdf',
		})).rejects.toBe(uploadError);

		const uploadCommand = mocks.send.mock.calls[0]?.[0];
		const cleanupCommand = mocks.send.mock.calls[1]?.[0];
		expect(uploadCommand).toBeInstanceOf(PutObjectCommand);
		expect(cleanupCommand).toBeInstanceOf(DeleteObjectCommand);
		expect(cleanupCommand.input.Key).toBe(uploadCommand.input.Key);
	});

	test('logs only safe cleanup metadata when compensation deletion fails', async () => {
		const uploadError = new Error('put failed');
		const cleanupError = new Error('cleanup failed with secret-like details');
		mocks.send.mockRejectedValueOnce(uploadError).mockRejectedValueOnce(cleanupError);
		const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);

		await expect(storeBookFile({
			fileType: 'epub',
			bytes: Buffer.from('PK'),
			mimeType: 'application/epub+zip',
			originalFileName: 'sample.epub',
		})).rejects.toBe(uploadError);

		expect(consoleError).toHaveBeenCalledWith('R2 upload cleanup failed', expect.objectContaining({
			key: expect.stringMatching(/^books\/[\da-f-]+\.epub$/),
			errorName: 'Error',
		}));
		expect(consoleError.mock.calls.flat().join(' ')).not.toContain('secret-like details');
		consoleError.mockRestore();
	});
});
