import { randomUUID } from 'node:crypto';
import { access, mkdir, realpath, rm, writeFile } from 'node:fs/promises';
import { isAbsolute, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
	DeleteObjectCommand,
	GetObjectCommand,
	HeadObjectCommand,
	PutObjectCommand,
	S3Client,
} from '@aws-sdk/client-s3';
import { getSignedUrl } from '@aws-sdk/s3-request-presigner';

export type BookFileType = 'epub' | 'pdf';
export type BookStorageDriver = 'local' | 'r2';

export const BOOK_FILE_SIGNED_URL_EXPIRES_IN_SECONDS = 60 * 60;

export type StoredBookFile = {
	fileUrl: string;
	storedFileName: string;
};

export type BookFileAccess =
	| { kind: 'local'; path: string }
	| { kind: 'signed-url'; url: string; expiresAt: string };

const R2_BOOK_OBJECT_KEY_PATTERN = /^books\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.(?:pdf|epub)$/;

export class BookStorageNotFoundError extends Error {
	constructor(message = 'Book file not found') {
		super(message);
		this.name = 'BookStorageNotFoundError';
	}
}

function getRequiredEnv(name: string) {
	const value = process.env[name]?.trim();
	if (!value) {
		throw new Error(`${name} is required when BOOK_FILE_STORAGE_DRIVER=r2`);
	}
	return value;
}

export function getBookStorageDriver(): BookStorageDriver {
	const driver = process.env.BOOK_FILE_STORAGE_DRIVER?.trim().toLowerCase();
	if (!driver || driver === 'local') {
		return 'local';
	}
	if (driver === 'r2') {
		return 'r2';
	}
	throw new Error('BOOK_FILE_STORAGE_DRIVER must be local or r2');
}

function getR2Config() {
	const accountId = process.env.R2_ACCOUNT_ID?.trim();
	const endpoint = process.env.R2_ENDPOINT?.trim() || (accountId ? `https://${accountId}.r2.cloudflarestorage.com` : '');
	if (!endpoint) {
		throw new Error('R2_ACCOUNT_ID or R2_ENDPOINT is required when BOOK_FILE_STORAGE_DRIVER=r2');
	}

	return {
		bucket: getRequiredEnv('R2_BUCKET_NAME'),
		endpoint,
		accessKeyId: getRequiredEnv('R2_ACCESS_KEY_ID'),
		secretAccessKey: getRequiredEnv('R2_SECRET_ACCESS_KEY'),
	};
}

export function validateBookStorageConfig() {
	if (getBookStorageDriver() === 'r2') {
		getR2Config();
	}
}

let cachedR2Client: { cacheKey: string; client: S3Client } | undefined;

function getR2Client() {
	const config = getR2Config();
	const cacheKey = [config.endpoint, config.accessKeyId, config.secretAccessKey].join('\n');
	if (!cachedR2Client || cachedR2Client.cacheKey !== cacheKey) {
		cachedR2Client?.client.destroy();
		cachedR2Client = {
			cacheKey,
			client: new S3Client({
				region: 'auto',
				endpoint: config.endpoint,
				credentials: {
					accessKeyId: config.accessKeyId,
					secretAccessKey: config.secretAccessKey,
				},
			}),
		};
	}
	return { client: cachedR2Client.client, bucket: config.bucket };
}

function isWithinRoot(root: string, candidate: string) {
	const relativePath = relative(root, candidate);
	return relativePath === '' || (!relativePath.startsWith('..') && !isAbsolute(relativePath));
}

function assertSafeRelativeStorageKey(key: string) {
	if (!key || key.includes('\0') || key.includes('\\') || isAbsolute(key) || /^[a-z][a-z\d+.-]*:/i.test(key)) {
		throw new BookStorageNotFoundError();
	}
	const parts = key.split('/');
	if (parts.some((part) => !part || part === '.' || part === '..')) {
		throw new BookStorageNotFoundError();
	}
	return key;
}

function assertR2BookObjectKey(key: string) {
	if (!R2_BOOK_OBJECT_KEY_PATTERN.test(key)) {
		throw new BookStorageNotFoundError();
	}
	return key;
}

async function getLocalStorageRoot(createIfMissing: boolean) {
	const configuredRoot = resolve(process.env.BOOK_FILE_STORAGE_ROOT ?? resolve(process.cwd(), 'storage'));
	if (createIfMissing) {
		await mkdir(configuredRoot, { recursive: true });
	}
	try {
		return await realpath(configuredRoot);
	} catch (error) {
		throw new BookStorageNotFoundError(error instanceof Error ? error.message : undefined);
	}
}

async function resolveLocalBookFilePath(fileUrl: string, requireExistingFile = true) {
	const rootPath = await getLocalStorageRoot(false);

	let requestedPath: string;
	if (fileUrl.startsWith('file://')) {
		const parsedUrl = new URL(fileUrl);
		if (parsedUrl.hostname) {
			throw new BookStorageNotFoundError();
		}
		requestedPath = fileURLToPath(parsedUrl);
	} else {
		assertSafeRelativeStorageKey(fileUrl);
		requestedPath = resolve(rootPath, fileUrl);
	}

	if (!isWithinRoot(rootPath, requestedPath)) {
		throw new BookStorageNotFoundError();
	}

	if (!requireExistingFile) {
		return requestedPath;
	}

	let resolvedPath: string;
	try {
		resolvedPath = await realpath(requestedPath);
		await access(resolvedPath);
	} catch (error) {
		throw new BookStorageNotFoundError(error instanceof Error ? error.message : undefined);
	}
	if (!isWithinRoot(rootPath, resolvedPath)) {
		throw new BookStorageNotFoundError();
	}
	return resolvedPath;
}

function isR2NotFoundError(error: unknown) {
	if (!error || typeof error !== 'object') {
		return false;
	}
	const candidate = error as { name?: unknown; $metadata?: { httpStatusCode?: unknown } };
	return candidate.name === 'NoSuchKey' || candidate.name === 'NotFound' || candidate.$metadata?.httpStatusCode === 404;
}

function makeContentDisposition(originalFileName: string) {
	return `inline; filename*=UTF-8''${encodeURIComponent(originalFileName)}`;
}

export async function storeBookFile(input: {
	fileType: BookFileType;
	bytes: Buffer;
	mimeType: string;
	originalFileName: string;
}): Promise<StoredBookFile> {
	const storedFileName = `${randomUUID()}.${input.fileType}`;
	if (getBookStorageDriver() === 'local') {
		const storageRoot = await getLocalStorageRoot(true);
		const storedPath = resolve(storageRoot, storedFileName);
		if (!isWithinRoot(storageRoot, storedPath)) {
			throw new Error('Storage path is invalid');
		}
		await writeFile(storedPath, input.bytes, { flag: 'wx' });
		return { fileUrl: storedFileName, storedFileName };
	}

	const objectKey = `books/${storedFileName}`;
	const { client, bucket } = getR2Client();
	try {
		await client.send(
			new PutObjectCommand({
				Bucket: bucket,
				Key: objectKey,
				Body: input.bytes,
				ContentType: input.mimeType,
				ContentLength: input.bytes.length,
				ContentDisposition: makeContentDisposition(input.originalFileName),
				CacheControl: 'private, no-store',
			}),
		);
	} catch (uploadError) {
		try {
			await client.send(new DeleteObjectCommand({ Bucket: bucket, Key: objectKey }));
		} catch (cleanupError) {
			console.error('R2 upload cleanup failed', {
				key: objectKey,
				errorName: cleanupError instanceof Error ? cleanupError.name : 'UnknownError',
			});
		}
		throw uploadError;
	}
	return { fileUrl: objectKey, storedFileName };
}

export async function deleteStoredBookFile(fileUrl: string) {
	if (getBookStorageDriver() === 'local') {
		const path = await resolveLocalBookFilePath(fileUrl, false);
		await rm(path, { force: true });
		return;
	}

	const key = assertR2BookObjectKey(assertSafeRelativeStorageKey(fileUrl));
	const { client, bucket } = getR2Client();
	await client.send(new DeleteObjectCommand({ Bucket: bucket, Key: key }));
}

export async function getBookFileAccess(fileUrl: string): Promise<BookFileAccess> {
	if (getBookStorageDriver() === 'local') {
		return { kind: 'local', path: await resolveLocalBookFilePath(fileUrl) };
	}

	const key = assertR2BookObjectKey(assertSafeRelativeStorageKey(fileUrl));
	const { client, bucket } = getR2Client();
	try {
		await client.send(new HeadObjectCommand({ Bucket: bucket, Key: key }));
	} catch (error) {
		if (isR2NotFoundError(error)) {
			throw new BookStorageNotFoundError();
		}
		throw error;
	}

	const expiresAt = new Date(Date.now() + BOOK_FILE_SIGNED_URL_EXPIRES_IN_SECONDS * 1000).toISOString();
	const url = await getSignedUrl(client, new GetObjectCommand({ Bucket: bucket, Key: key }), {
		expiresIn: BOOK_FILE_SIGNED_URL_EXPIRES_IN_SECONDS,
	});
	return { kind: 'signed-url', url, expiresAt };
}
