import { createHash, randomUUID } from 'node:crypto';
import type { Context } from 'hono';
import { Hono } from 'hono';
import { auth } from '../../lib/auth.js';
import { createBookFileStorage } from '../../lib/bookFileStorage.js';
import type { PrismaVariables } from '../../lib/prisma.js';

const app = new Hono<PrismaVariables>();
type AdminContext = Context<PrismaVariables>;
const PDF_MIME_TYPE = 'application/pdf';
export const MAX_PDF_FILE_SIZE = 200 * 1024 * 1024;

type PdfUpload = {
	name: string;
	type: string;
	size: number;
	arrayBuffer: () => Promise<ArrayBuffer>;
};

type ValidationResult = { valid: true; file: PdfUpload } | { valid: false; status: 400 | 413; message: string; code: string };
type AdminBookCreateBody = {
	title?: unknown;
	authorName?: unknown;
	publisher?: unknown;
	publishedAt?: unknown;
	categoryId?: unknown;
	pageTurnDirection?: unknown;
	description?: unknown;
};

export function validatePdfUploadMetadata(value: unknown): ValidationResult {
	if (
		!value ||
		typeof value !== 'object' ||
		typeof (value as PdfUpload).name !== 'string' ||
		typeof (value as PdfUpload).type !== 'string' ||
		typeof (value as PdfUpload).size !== 'number' ||
		typeof (value as PdfUpload).arrayBuffer !== 'function'
	) {
		return { valid: false, status: 400, message: 'A single PDF file is required', code: 'INVALID_FILE' };
	}

	const file = value as PdfUpload;
	if (file.type.toLowerCase() !== PDF_MIME_TYPE || !file.name.toLowerCase().endsWith('.pdf')) {
		return { valid: false, status: 400, message: 'Only PDF files are supported', code: 'INVALID_PDF' };
	}
	if (file.name.length > 255) {
		return { valid: false, status: 400, message: 'PDF filename is too long', code: 'INVALID_FILE_NAME' };
	}
	if (!Number.isSafeInteger(file.size) || file.size < 1) {
		return { valid: false, status: 400, message: 'PDF file size is invalid', code: 'INVALID_FILE_SIZE' };
	}
	if (file.size > MAX_PDF_FILE_SIZE) {
		return { valid: false, status: 413, message: 'PDF file exceeds the 200MB limit', code: 'FILE_TOO_LARGE' };
	}

	return { valid: true, file };
}

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
	if (value === undefined || value === null) {
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

function parseAdminBookCreateBody(body: unknown) {
	if (!body || typeof body !== 'object' || Array.isArray(body)) {
		throw new Error('Request body must be an object');
	}

	const input = body as AdminBookCreateBody;
	const allowedFields = new Set(['title', 'authorName', 'publisher', 'publishedAt', 'categoryId', 'pageTurnDirection', 'description']);
	if (Object.keys(input).some((field) => !allowedFields.has(field))) {
		throw new Error('Request body contains an unsupported field');
	}
	if (typeof input.title !== 'string' || !input.title.trim() || input.title.trim().length > 255) {
		throw new Error('title is required and must be 255 characters or fewer');
	}
	if (typeof input.categoryId !== 'number' || !Number.isSafeInteger(input.categoryId) || input.categoryId < 1) {
		throw new Error('categoryId is required');
	}
	if (input.pageTurnDirection !== undefined && input.pageTurnDirection !== 'ltr' && input.pageTurnDirection !== 'rtl') {
		throw new Error('pageTurnDirection must be ltr or rtl');
	}

	return {
		title: input.title.trim(),
		authorName: normalizeOptionalString(input.authorName, 'authorName', 255),
		publisher: normalizeOptionalString(input.publisher, 'publisher', 255),
		publishedAt: parsePublishedAt(input.publishedAt),
		categoryId: input.categoryId,
		pageTurnDirection: input.pageTurnDirection === undefined ? 'ltr' : input.pageTurnDirection,
		description: normalizeOptionalString(input.description, 'description'),
	};
}

app.get('/categories', async (c) => {
	try {
		const admin = await getAdminUser(c);
		if ('response' in admin) {
			return admin.response;
		}

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

app.post('/books', async (c) => {
	try {
		const admin = await getAdminUser(c);
		if ('response' in admin) {
			return admin.response;
		}

		let body: unknown;
		try {
			body = await c.req.json();
		} catch {
			return jsonError(c, 400, 'Request body must be valid JSON', 'INVALID_REQUEST');
		}

		let input: ReturnType<typeof parseAdminBookCreateBody>;
		try {
			input = parseAdminBookCreateBody(body);
		} catch {
			return jsonError(c, 400, 'Book metadata is invalid', 'INVALID_BOOK');
		}

		const category = await c.get('prisma').category.findUnique({
			where: { id: input.categoryId },
			select: { id: true, isActive: true },
		});
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

app.post('/books/:bookId/files', async (c) => {
	let storedKey: string | undefined;
	let storage: ReturnType<typeof createBookFileStorage> | undefined;
	let storageWritten = false;
	try {
		const admin = await getAdminUser(c);
		if ('response' in admin) {
			return admin.response;
		}

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

		let body: Record<string, unknown>;
		try {
			body = await c.req.parseBody();
		} catch {
			return jsonError(c, 400, 'Request body must be multipart form data', 'INVALID_REQUEST');
		}
		const validation = validatePdfUploadMetadata(body.file);
		if (!validation.valid) {
			return jsonError(c, validation.status, validation.message, validation.code);
		}

		const bytes = Buffer.from(await validation.file.arrayBuffer());
		if (bytes.length > MAX_PDF_FILE_SIZE) {
			return jsonError(c, 413, 'PDF file exceeds the 200MB limit', 'FILE_TOO_LARGE');
		}
		if (bytes.length < 5 || bytes.subarray(0, 5).toString('ascii') !== '%PDF-') {
			return jsonError(c, 400, 'Uploaded file is not a valid PDF', 'INVALID_PDF');
		}

		const fileHash = createHash('sha256').update(bytes).digest('hex');
		const duplicate = await c.get('prisma').bookFile.findFirst({ where: { fileHash }, select: { id: true } });
		if (duplicate) {
			return jsonError(c, 409, 'The same PDF file is already registered', 'DUPLICATE_FILE');
		}

		const storedFileName = `${randomUUID()}.pdf`;
		storedKey = storedFileName;
		storage = createBookFileStorage();
		await storage.put(storedKey, bytes, PDF_MIME_TYPE);
		storageWritten = true;
		const record = await c.get('prisma').bookFile.create({
			data: {
				extension: 'pdf',
				mimeType: PDF_MIME_TYPE,
				fileUrl: storedFileName,
				originalFileName: validation.file.name,
				storedFileName,
				fileSize: bytes.length,
				fileHash,
				bookId,
			},
		});
		return c.json(record, 201);
	} catch (error) {
		if (storageWritten && storage && storedKey) {
			try {
				await storage.delete(storedKey);
			} catch (cleanupError) {
				console.error('Failed to clean up uploaded storage object', cleanupError);
			}
		}
		console.error(error);
		return c.json({ error: 'Failed to register PDF file' }, 500);
	}
});

export default app;
