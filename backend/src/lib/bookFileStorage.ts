import { GetObjectCommand, PutObjectCommand, DeleteObjectCommand, S3Client } from '@aws-sdk/client-s3';
import { createReadStream } from 'node:fs';
import { mkdir, realpath, rm, writeFile } from 'node:fs/promises';
import { isAbsolute, relative, resolve } from 'node:path';
import { Readable } from 'node:stream';
import { ReadableStream as NodeReadableStream } from 'node:stream/web';

export type BookFileStorage = {
	put: (key: string, body: Buffer, contentType: string) => Promise<void>;
	get: (key: string) => Promise<Readable>;
	delete: (key: string) => Promise<void>;
};

type StorageEnvironment = Record<string, string | undefined>;

function assertSafeKey(key: string) {
	if (
		!key ||
		key.includes('\0') ||
		key.split('/').some((segment) => segment === '..' || segment === '.') ||
		isAbsolute(key) ||
		/^[a-z][a-z\d+.-]*:/i.test(key)
	) {
		throw new Error('Only relative storage keys are allowed');
	}
}

function localStorage(environment: StorageEnvironment): BookFileStorage {
	const root = resolve(environment.BOOK_FILE_STORAGE_ROOT ?? resolve(process.cwd(), 'storage'));

	function assertWithinRoot(rootPath: string, candidate: string) {
		const pathFromRoot = relative(rootPath, candidate);
		if (pathFromRoot.startsWith('..') || isAbsolute(pathFromRoot)) {
			throw new Error('Storage key escapes storage root');
		}
	}

	async function ensureRoot() {
		await mkdir(root, { recursive: true });
		return realpath(root);
	}

	function resolveKey(rootPath: string, key: string) {
		assertSafeKey(key);
		const candidate = resolve(rootPath, key);
		assertWithinRoot(rootPath, candidate);
		return candidate;
	}

	async function resolveExistingKey(key: string) {
		const rootPath = await ensureRoot();
		const candidate = resolveKey(rootPath, key);
		const resolved = await realpath(candidate);
		assertWithinRoot(rootPath, resolved);
		return resolved;
	}

	return {
		async put(key, body) {
			const rootPath = await ensureRoot();
			const path = resolveKey(rootPath, key);
			await mkdir(resolve(path, '..'), { recursive: true });
			const parent = await realpath(resolve(path, '..'));
			assertWithinRoot(rootPath, parent);
			await writeFile(path, body, { flag: 'wx' });
		},
		async get(key) {
			return createReadStream(await resolveExistingKey(key));
		},
		async delete(key) {
			const rootPath = await ensureRoot();
			await rm(resolveKey(rootPath, key), { force: true });
		},
	};
}

function r2Storage(environment: StorageEnvironment): BookFileStorage {
	const endpoint = environment.R2_ENDPOINT;
	const bucket = environment.R2_BUCKET_NAME;
	const accessKeyId = environment.R2_ACCESS_KEY_ID;
	const secretAccessKey = environment.R2_SECRET_ACCESS_KEY;
	if (!endpoint || !bucket || !accessKeyId || !secretAccessKey) {
		throw new Error('R2 storage requires R2_ENDPOINT, R2_BUCKET_NAME, R2_ACCESS_KEY_ID, and R2_SECRET_ACCESS_KEY');
	}

	const client = new S3Client({
		endpoint,
		region: 'auto',
		credentials: { accessKeyId, secretAccessKey },
	});

	return {
		async put(key, body, contentType) {
			assertSafeKey(key);
			await client.send(new PutObjectCommand({ Bucket: bucket, Key: key, Body: body, ContentType: contentType }));
		},
		async get(key) {
			assertSafeKey(key);
			const response = await client.send(new GetObjectCommand({ Bucket: bucket, Key: key }));
			if (!response.Body) {
				throw new Error('R2 object body was empty');
			}
			if (response.Body instanceof Readable) {
				return response.Body;
			}
			if (typeof response.Body === 'object' && 'getReader' in response.Body) {
				return Readable.fromWeb(response.Body as unknown as NodeReadableStream);
			}
			return Readable.from(response.Body as unknown as AsyncIterable<Uint8Array>);
		},
		async delete(key) {
			assertSafeKey(key);
			await client.send(new DeleteObjectCommand({ Bucket: bucket, Key: key }));
		},
	};
}

export function createBookFileStorage(environment: StorageEnvironment = process.env): BookFileStorage {
	const driver = (environment.BOOK_FILE_STORAGE_DRIVER ?? 'local').toLowerCase();
	if (driver === 'r2') {
		return r2Storage(environment);
	}
	if (driver === 'local') {
		return localStorage(environment);
	}
	throw new Error(`Unsupported book file storage driver: ${environment.BOOK_FILE_STORAGE_DRIVER}`);
}
