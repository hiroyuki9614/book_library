import { createHash, randomUUID } from 'node:crypto';
import { mkdir, realpath, rm, writeFile } from 'node:fs/promises';
import { isAbsolute, relative, resolve } from 'node:path';
import type { Context } from 'hono';
import { Hono } from 'hono';
import { auth } from '../../lib/auth.js';
import type { PrismaVariables } from '../../lib/prisma.js';

const app = new Hono<PrismaVariables>();
type AdminContext = Context<PrismaVariables>;

const BOOK_FILE_MIME_TYPES = {
	pdf: 'application/pdf',
	epub: 'application/epub+zip',
} as const;

type SupportedBookFileType = keyof typeof BOOK_FILE_MIME_TYPES;
type PublicationScope = 'all_users' | 'admin_only';

export const MAX_BOOK_FILE_SIZE = 200 * 1024 * 1024;

export type BookFileUpload = {
	name: string;
	type: string;
	size: number;
	arrayBuffer: () => Promise<ArrayBuffer>;
};

type FileValidationResult =
	| { valid: true; file: BookFileUpload; fileType: SupportedBookFileType; mimeType: string }
	| { valid: false; status: 400 | 413; message: string; code: string };

type PreparedBookFileResult =
	| {
			valid: true;
			file: BookFileUpload;
			fileType: SupportedBookFileType;
			mimeType: string;
			bytes: Buffer;
			fileHash: string;
	  }
	| { valid: false; status: 400 | 413; message: string; code: string };

type AdminBookCreateBody = {
	title?: unknown;
	authorName?: unknown;
	publisher?: unknown;
	publishedAt?: unknown;
	categoryId?: unknown;
	pageTurnDirection?: unknown;
	description?: unknown;
	publicationScope?: unknown;
};

type ParsedBookMetadata = {
	title: string;
	authorName: string | null;
	publisher: string | null;
	publishedAt: Date | null;
	categoryId: number;
	pageTurnDirection: 'ltr' | 'rtl';
	description: string | null;
	publicationScope: PublicationScope;
};

function jsonError(c: AdminContext, status: 400 | 401 | 403 | 404 | 409 | 413 | 500, message: string, code: string) {
	return c.json({ message, code }, status);
}

async function getAdminUser(c: AdminContext) {
	const session = await auth.api.getSession({ headers: c.req.raw.headers });
	if (!session) {
		return { response: jsonError(c, 401, 'Authentication required', 'UNAUTHORIZED') };
	}

	const userId = Number(session.user.id);
	if (!Number.isSafeInteger(userId)) {
		return { response: jsonError(c, 401, 'Authentication required', 'UNAUTHORIZED') };
	}

	const user = await c.get('prisma').user.findFirst({
		where: { id: userId, deletedAt: null },
		select: { id: true, role: { select: { name: true } } },
	});
	if (!user || user.role.name !== 'admin') {
		return { response: jsonError(c, 403, 'Administrator role required', 'FORBIDDEN') };
	}

	return { user };
}

function normalizeOptionalString(value: unknown, fieldName: string, maxLength?: number): string | null {
	if (value === undefined || value === null || value === '') {
		return null;
	}
	if (typeof value !== 'string') {
		throw new Error(`${fieldName} must be a string`);
	}

	const normalized = value.trim();
	if (maxLength !== undefined && normalized.length > maxLength) {
		throw new Error(`${fieldName} is too long`);
	}
	return normalized || null;
}

function parsePublishedAt(value: unknown): Date | null {
	if (value === undefined || value === null || value === '') {
		return null;
	}
	if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
		throw new Error('publishedAt must be a valid date');
	}

	const parsed = new Date(`${value}T00:00:00.000Z`);
	const [year, month, day] = value.split('-').map(Number);
	if (
		!Number.isFinite(parsed.getTime()) ||
		parsed.getUTCFullYear() !== year ||
		parsed.getUTCMonth() + 1 !== month ||
		parsed.getUTCDate() !== day
	) {
		throw new Error('publishedAt must be a valid date');
	}
	return parsed;
}

function parseCategoryId(value: unknown): number {
	const parsed = typeof value === 'string' && /^\d+$/.test(value) ? Number(value) : value;
	if (typeof parsed !== 'number' || !Number.isSafeInteger(parsed) || parsed < 1) {
		throw new Error('categoryId is required');
	}
	return parsed;
}

function parsePublicationScope(value: unknown, allowLegacyDefault = false): PublicationScope {
	if (value === 'all_users' || value === 'admin_only') {
		return value;
	}
	if (allowLegacyDefault && value === undefined) {
		return 'all_users';
	}
	throw new Error('publicationScope is required');
}

function parseAdminBookCreateBody(body: unknown, allowLegacyPublicationDefault = false): ParsedBookMetadata {
	if (!body || typeof body !== 'object' || Array.isArray(body)) {
		throw new Error('Request body must be an object');
	}

	const input = body as AdminBookCreateBody;
	const allowedFields = new Set([
		'title',
		'authorName',
		'publisher',
		'publishedAt',
		'categoryId',
		'pageTurnDirection',
		'description',
		'publicationScope',
	]);
	if (Object.keys(input).some((field) => !allowedFields.has(field))) {
		throw new Error('Request body contains an unsupported field');
	}
	if (typeof input.title !== 'string' || !input.title.trim() || input.title.trim().length > 255) {
		throw new Error('title is required and must be 255 characters or fewer');
	}
	if (input.pageTurnDirection !== undefined && input.pageTurnDirection !== 'ltr' && input.pageTurnDirection !== 'rtl') {
		throw new Error('pageTurnDirection must be ltr or rtl');
	}

	return {
		title: input.title.trim(),
		authorName: normalizeOptionalString(input.authorName, 'authorName', 255),
		publisher: normalizeOptionalString(input.publisher, 'publisher', 255),
		publishedAt: parsePublishedAt(input.publishedAt),
		categoryId: parseCategoryId(input.categoryId),
		pageTurnDirection: input.pageTurnDirection === 'rtl' ? 'rtl' : 'ltr',
		description: normalizeOptionalString(input.description, 'description'),
		publicationScope: parsePublicationScope(input.publicationScope, allowLegacyPublicationDefault),
	};
}

function permissionCreatesForScope(publicationScope: PublicationScope) {
	const roleNames = publicationScope === 'all_users' ? ['admin', 'user'] : ['admin'];
	return roleNames.map((name) => ({ role: { connect: { name } } }));
}

export function validateBookFileUploadMetadata(value: unknown): FileValidationResult {
	if (
		!value ||
		typeof value !== 'object' ||
		typeof (value as BookFileUpload).name !== 'string' ||
		typeof (value as BookFileUpload).type !== 'string' ||
		typeof (value as BookFileUpload).size !== 'number' ||
		typeof (value as BookFileUpload).arrayBuffer !== 'function'
	) {
		return { valid: false, status: 400, message: 'A single EPUB or PDF file is required', code: 'INVALID_FILE' };
	}

	const file = value as BookFileUpload;
	const lowerName = file.name.toLowerCase();
	const mimeType = file.type.split(';', 1)[0].trim().toLowerCase();
	const fileType: SupportedBookFileType | null =
		lowerName.endsWith('.pdf') && mimeType === BOOK_FILE_MIME_TYPES.pdf
			? 'pdf'
			: lowerName.endsWith('.epub') && mimeType === BOOK_FILE_MIME_TYPES.epub
				? 'epub'
				: null;

	if (!fileType) {
		return { valid: false, status: 400, message: 'Only EPUB or PDF files are supported', code: 'UNSUPPORTED_BOOK_FILE' };
	}
	if (file.name.length > 255) {
		return { valid: false, status: 400, message: 'Book filename is too long', code: 'INVALID_FILE_NAME' };
	}
	if (!Number.isSafeInteger(file.size) || file.size < 1) {
		return { valid: false, status: 400, message: 'Book file size is invalid', code: 'INVALID_FILE_SIZE' };
	}
	if (file.size > MAX_BOOK_FILE_SIZE) {
		return { valid: false, status: 413, message: 'Book file exceeds the 200MB limit', code: 'FILE_TOO_LARGE' };
	}

	return { valid: true, file, fileType, mimeType: BOOK_FILE_MIME_TYPES[fileType] };
}

function isValidPdf(bytes: Buffer) {
	return bytes.length >= 5 && bytes.subarray(0, 5).toString('ascii') === '%PDF-';
}

function isValidEpub(bytes: Buffer) {
	if (bytes.length < 38 || bytes.readUInt32LE(0) !== 0x04034b50) {
		return false;
	}

	const compressionMethod = bytes.readUInt16LE(8);
	const compressedSize = bytes.readUInt32LE(18);
	const uncompressedSize = bytes.readUInt32LE(22);
	const fileNameLength = bytes.readUInt16LE(26);
	const extraFieldLength = bytes.readUInt16LE(28);
	const fileNameStart = 30;
	const fileNameEnd = fileNameStart + fileNameLength;
	const contentStart = fileNameEnd + extraFieldLength;
	const contentEnd = contentStart + compressedSize;

	if (compressionMethod !== 0 || compressedSize !== uncompressedSize || fileNameEnd > bytes.length || contentEnd > bytes.length) {
		return false;
	}
	if (bytes.subarray(fileNameStart, fileNameEnd).toString('utf8') !== 'mimetype') {
		return false;
	}

	return bytes.subarray(contentStart, contentEnd).toString('ascii') === BOOK_FILE_MIME_TYPES.epub;
}

function validateBookFileContents(fileType: SupportedBookFileType, bytes: Buffer) {
	return fileType === 'pdf' ? isValidPdf(bytes) : isValidEpub(bytes);
}

async function prepareBookFile(value: unknown): Promise<PreparedBookFileResult> {
	const validation = validateBookFileUploadMetadata(value);
	if (validation.valid === false) {
		return validation;
	}

	const bytes = Buffer.from(await validation.file.arrayBuffer());
	if (bytes.length > MAX_BOOK_FILE_SIZE) {
		return { valid: false, status: 413, message: 'Book file exceeds the 200MB limit', code: 'FILE_TOO_LARGE' };
	}
	if (bytes.length !== validation.file.size) {
		return { valid: false, status: 400, message: 'Book file size does not match upload metadata', code: 'INVALID_FILE_SIZE' };
	}
	if (!validateBookFileContents(validation.fileType, bytes)) {
		return {
			valid: false,
			status: 400,
			message: `Uploaded file is not a valid ${validation.fileType.toUpperCase()}`,
			code: 'INVALID_BOOK_FILE',
		};
	}

	return {
		valid: true,
		file: validation.file,
		fileType: validation.fileType,
		mimeType: validation.mimeType,
		bytes,
		fileHash: createHash('sha256').update(bytes).digest('hex'),
	};
}

async function storeBookFile(fileType: SupportedBookFileType, bytes: Buffer) {
	const storageRoot = resolve(process.env.BOOK_FILE_STORAGE_ROOT ?? resolve(process.cwd(), 'storage'));
	await mkdir(storageRoot, { recursive: true });
	const storedRoot = await realpath(storageRoot);
	const storedFileName = `${randomUUID()}.${fileType}`;
	const storedPath = resolve(storedRoot, storedFileName);
	const pathFromRoot = relative(storedRoot, storedPath);
	if (pathFromRoot.startsWith('..') || isAbsolute(pathFromRoot)) {
		throw new Error('Storage path is invalid');
	}

	await writeFile(storedPath, bytes, { flag: 'wx' });
	return { storedPath, storedFileName };
}

function isUniqueConstraintError(error: unknown) {
	return Boolean(error && typeof error === 'object' && 'code' in error && (error as { code?: unknown }).code === 'P2002');
}

async function findActiveCategory(c: AdminContext, categoryId: number) {
	return c.get('prisma').category.findUnique({
		where: { id: categoryId },
		select: { id: true, isActive: true },
	});
}

app.get('/categories', async (c) => {
	try {
		const admin = await getAdminUser(c);
		if ('response' in admin) return admin.response;

		const categories = await c.get('prisma').category.findMany({
			where: { isActive: true },
			orderBy: [{ displayOrder: 'asc' }, { id: 'asc' }],
			select: { id: true, name: true },
		});
		return c.json({ categories });
	} catch (error) {
		console.error(error);
		return c.json({ error: 'Failed to load categories' }, 500);
	}
});

// Legacy metadata-only vertical-slice endpoint. The full Admin UI uses /book-registrations.
app.post('/books', async (c) => {
	try {
		const admin = await getAdminUser(c);
		if ('response' in admin) return admin.response;

		let body: unknown;
		try {
			body = await c.req.json();
		} catch {
			return jsonError(c, 400, 'Request body must be valid JSON', 'INVALID_REQUEST');
		}

		let input: ParsedBookMetadata;
		try {
			input = parseAdminBookCreateBody(body, true);
		} catch {
			return jsonError(c, 400, 'Book metadata is invalid', 'INVALID_BOOK');
		}

		const category = await findActiveCategory(c, input.categoryId);
		if (!category || !category.isActive) {
			return jsonError(c, 400, 'Active category is required', 'INVALID_CATEGORY');
		}

		const book = await c.get('prisma').book.create({
			data: {
				title: input.title,
				authorName: input.authorName,
				publisher: input.publisher,
				publishedAt: input.publishedAt,
				pageTurnDirection: input.pageTurnDirection,
				description: input.description,
				category: { connect: { id: input.categoryId } },
				roleBookPermissions: { create: [{ role: { connect: { name: 'user' } } }] },
			},
			select: {
				id: true,
				title: true,
				authorName: true,
				publisher: true,
				publishedAt: true,
				categoryId: true,
				pageTurnDirection: true,
				description: true,
				category: { select: { id: true, name: true } },
			},
		});
		return c.json(book, 201);
	} catch (error) {
		console.error(error);
		return c.json({ error: 'Failed to register book' }, 500);
	}
});

app.post('/book-registrations', async (c) => {
	let storedPath: string | undefined;
	try {
		const admin = await getAdminUser(c);
		if ('response' in admin) return admin.response;

		let body: Record<string, unknown>;
		try {
			body = (await c.req.parseBody()) as Record<string, unknown>;
		} catch {
			return jsonError(c, 400, 'Request body must be multipart form data', 'INVALID_REQUEST');
		}

		const { file, ...metadataBody } = body;
		let input: ParsedBookMetadata;
		try {
			input = parseAdminBookCreateBody(metadataBody);
		} catch {
			return jsonError(c, 400, 'Book metadata is invalid', 'INVALID_BOOK');
		}

		const category = await findActiveCategory(c, input.categoryId);
		if (!category || !category.isActive) {
			return jsonError(c, 400, 'Active category is required', 'INVALID_CATEGORY');
		}

		const preparedFile = await prepareBookFile(file);
		if (preparedFile.valid === false) {
			return jsonError(c, preparedFile.status, preparedFile.message, preparedFile.code);
		}

		const duplicate = await c.get('prisma').bookFile.findUnique({
			where: { fileHash: preparedFile.fileHash },
			select: { id: true },
		});
		if (duplicate) {
			return jsonError(c, 409, 'The same book file is already registered', 'DUPLICATE_FILE');
		}

		const stored = await storeBookFile(preparedFile.fileType, preparedFile.bytes);
		storedPath = stored.storedPath;

		const book = await c.get('prisma').book.create({
			data: {
				title: input.title,
				authorName: input.authorName,
				publisher: input.publisher,
				publishedAt: input.publishedAt,
				pageTurnDirection: input.pageTurnDirection,
				description: input.description,
				category: { connect: { id: input.categoryId } },
				roleBookPermissions: { create: permissionCreatesForScope(input.publicationScope) },
				bookFiles: {
					create: [
						{
							extension: preparedFile.fileType,
							mimeType: preparedFile.mimeType,
							fileUrl: stored.storedFileName,
							originalFileName: preparedFile.file.name,
							storedFileName: stored.storedFileName,
							fileSize: preparedFile.bytes.length,
							fileHash: preparedFile.fileHash,
						},
					],
				},
			},
			select: {
				id: true,
				title: true,
				authorName: true,
				publisher: true,
				publishedAt: true,
				categoryId: true,
				pageTurnDirection: true,
				description: true,
				category: { select: { id: true, name: true } },
				bookFiles: {
					select: { id: true, extension: true, mimeType: true, originalFileName: true, fileSize: true },
					take: 1,
				},
			},
		});

		const { bookFiles, ...bookMetadata } = book;
		return c.json({ ...bookMetadata, file: bookFiles[0], publicationScope: input.publicationScope }, 201);
	} catch (error) {
		if (storedPath) await rm(storedPath, { force: true });
		if (isUniqueConstraintError(error)) {
			return jsonError(c, 409, 'The same book file is already registered', 'DUPLICATE_FILE');
		}
		console.error(error);
		return c.json({ error: 'Failed to register book and file' }, 500);
	}
});

app.post('/books/:bookId/files', async (c) => {
	let storedPath: string | undefined;
	try {
		const admin = await getAdminUser(c);
		if ('response' in admin) return admin.response;

		const bookId = Number(c.req.param('bookId'));
		if (!Number.isSafeInteger(bookId) || bookId < 1) {
			return jsonError(c, 404, 'Book not found', 'BOOK_NOT_FOUND');
		}

		const book = await c.get('prisma').book.findUnique({
			where: { id: bookId },
			select: { id: true, deletedAt: true },
		});
		if (!book || book.deletedAt) {
			return jsonError(c, 404, 'Book not found', 'BOOK_NOT_FOUND');
		}

		const existingFile = await c.get('prisma').bookFile.findFirst({
			where: { bookId },
			select: { id: true },
		});
		if (existingFile) {
			return jsonError(c, 409, 'Only one book file can be registered per book in the MVP', 'BOOK_FILE_ALREADY_EXISTS');
		}

		let body: Record<string, unknown>;
		try {
			body = (await c.req.parseBody()) as Record<string, unknown>;
		} catch {
			return jsonError(c, 400, 'Request body must be multipart form data', 'INVALID_REQUEST');
		}

		const preparedFile = await prepareBookFile(body.file);
		if (preparedFile.valid === false) {
			return jsonError(c, preparedFile.status, preparedFile.message, preparedFile.code);
		}

		const duplicate = await c.get('prisma').bookFile.findUnique({
			where: { fileHash: preparedFile.fileHash },
			select: { id: true },
		});
		if (duplicate) {
			return jsonError(c, 409, 'The same book file is already registered', 'DUPLICATE_FILE');
		}

		const stored = await storeBookFile(preparedFile.fileType, preparedFile.bytes);
		storedPath = stored.storedPath;

		const record = await c.get('prisma').bookFile.create({
			data: {
				extension: preparedFile.fileType,
				mimeType: preparedFile.mimeType,
				fileUrl: stored.storedFileName,
				originalFileName: preparedFile.file.name,
				storedFileName: stored.storedFileName,
				fileSize: preparedFile.bytes.length,
				fileHash: preparedFile.fileHash,
				bookId,
			},
		});
		return c.json(record, 201);
	} catch (error) {
		if (storedPath) await rm(storedPath, { force: true });
		if (isUniqueConstraintError(error)) {
			return jsonError(c, 409, 'The same book file is already registered', 'DUPLICATE_FILE');
		}
		console.error(error);
		return c.json({ error: 'Failed to register book file' }, 500);
	}
});

export default app;
