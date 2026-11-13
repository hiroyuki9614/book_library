import { Hono } from 'hono';
import type { Context } from 'hono';
import { createReadStream } from 'node:fs';
import { Readable } from 'node:stream';
import { auth } from '../../lib/auth.js';
import { BookStorageNotFoundError, getBookFileAccess } from '../../lib/bookStorage.js';
import type { PrismaVariables } from '../../lib/prisma.js';
import { hasReadingSessionUserMismatch, MAX_CURRENT_POSITION_LENGTH, normalizeProgressPercentage, normalizeReadStatus, persistReadingInfo, READING_SESSION_USER_HEADER, toReadingInfoResponse, toReadingProgress, type ReadingProgressRecord } from './readingProgress.js';

const app = new Hono<PrismaVariables>();

type BooksContext = Context<PrismaVariables>;

type BookWithRelations = {
	id: number;
	title: string;
	authorName: string | null;
	publishedAt: Date | null;
	publisher: string | null;
	description: string | null;
	deletedAt: Date | null;
	pageTurnDirection: string;
	createdAt: Date;
	updatedAt: Date;
	category: { id: number; name: string };
	bookFiles: Array<{
		extension: string;
		mimeType: string;
		fileSize: number;
		originalFileName: string;
		fileUrl: string;
	}>;
	readingInfos?: ReadingProgressRecord[];
};

type BookFileRecord = BookWithRelations['bookFiles'][number];
type SupportedBookFileType = 'epub' | 'pdf';

function jsonError(c: BooksContext, status: 400 | 401 | 403 | 404 | 409 | 500, message: string, code: string) {
	return c.json({ message, code }, status);
}

async function getAuthenticatedUser(c: BooksContext) {
	const session = await auth.api.getSession({ headers: c.req.raw.headers });
	if (!session) {
		return null;
	}

	const userId = Number(session.user.id);
	if (!Number.isSafeInteger(userId)) {
		return null;
	}

	const user = await c.get('prisma').user.findFirst({
		where: { id: userId },
		select: { id: true, roleId: true },
	});

	return user;
}

function parseBookId(rawBookId: string | undefined) {
	if (!rawBookId || !/^[1-9]\d*$/.test(rawBookId)) {
		return null;
	}

	const bookId = Number(rawBookId);
	return Number.isSafeInteger(bookId) ? bookId : null;
}

function isJsonObject(value: unknown): value is Record<string, unknown> {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function getSupportedBookFileType(file: BookFileRecord): SupportedBookFileType | null {
	const extension = file.extension.trim().toLowerCase();
	const mimeType = file.mimeType.split(';', 1)[0].trim().toLowerCase();

	if (extension === 'epub' && mimeType === 'application/epub+zip') {
		return 'epub';
	}
	if (extension === 'pdf' && mimeType === 'application/pdf') {
		return 'pdf';
	}
	return null;
}

function getPreferredBookFile(book: BookWithRelations) {
	const epubFile = book.bookFiles.find((file) => getSupportedBookFileType(file) === 'epub');
	if (epubFile) {
		return { file: epubFile, fileType: 'epub' as const };
	}

	const pdfFile = book.bookFiles.find((file) => getSupportedBookFileType(file) === 'pdf');
	if (pdfFile) {
		return { file: pdfFile, fileType: 'pdf' as const };
	}

	return null;
}

function toBookResponse(book: BookWithRelations, readingInfo: ReadingProgressRecord | null = null) {
	const preferredFile = getPreferredBookFile(book);
	const file = preferredFile?.file;
	return {
		id: book.id,
		title: book.title,
		authorName: book.authorName,
		publishedAt: book.publishedAt?.toISOString() ?? null,
		publisher: book.publisher,
		description: book.description,
		category: book.category,
		pageTurnDirection: book.pageTurnDirection,
		readStatus: normalizeReadStatus(readingInfo?.readStatus),
		progressPercentage: toReadingProgress(readingInfo),
		hasFile: Boolean(file),
		fileType: preferredFile?.fileType ?? null,
		fileSize: file?.fileSize ?? null,
		originalFileName: file?.originalFileName ?? null,
		createdAt: book.createdAt.toISOString(),
		updatedAt: book.updatedAt.toISOString(),
	};
}

async function getBookForAuthorizedUser(c: BooksContext, bookId: number, userId: number, roleId: number) {
	const prisma = c.get('prisma');
	const book = await prisma.book.findUnique({
		where: { id: bookId },
		include: { category: true, bookFiles: { orderBy: { id: 'asc' } } },
	});

	if (!book || book.deletedAt) {
		return { response: jsonError(c, 404, 'Book not found', 'BOOK_NOT_FOUND') };
	}

	const permission = await prisma.roleBookPermission.findUnique({
		where: { roleId_bookId: { roleId, bookId } },
	});
	if (!permission) {
		return { response: jsonError(c, 403, 'You do not have permission to view this book', 'FORBIDDEN') };
	}

	return { book, prisma, userId };
}

async function getAuthorizedBook(c: BooksContext, bookId: number, requireReadingSession = false) {
	const user = await getAuthenticatedUser(c);
	if (!user) {
		return { response: jsonError(c, 401, 'Authentication required', 'UNAUTHORIZED') };
	}

	const result = await getBookForAuthorizedUser(c, bookId, user.id, user.roleId);
	if ('response' in result) {
		return result;
	}
	if (requireReadingSession && hasReadingSessionUserMismatch(c.req.header(READING_SESSION_USER_HEADER), user.id)) {
		return { response: jsonError(c, 409, 'Reading request is bound to a different authenticated user', 'READING_SESSION_MISMATCH') };
	}

	return result;
}

app.get('/', async (c) => {
	try {
		const user = await getAuthenticatedUser(c);
		if (!user) {
			return jsonError(c, 401, 'Authentication required', 'UNAUTHORIZED');
		}

		const prisma = c.get('prisma');
		const page = Math.max(1, Number(c.req.query('page') ?? 1) || 1);
		const limit = Math.min(100, Math.max(1, Number(c.req.query('limit') ?? 20) || 20));
		const where = {
			deletedAt: null,
			roleBookPermissions: { some: { roleId: user.roleId } },
		};
		const [total, books] = await Promise.all([
			prisma.book.count({ where }),
			prisma.book.findMany({
				where,
				include: {
					category: true,
					bookFiles: { orderBy: { id: 'asc' } },
					readingInfos: { where: { userId: user.id }, take: 1 },
				},
				orderBy: { createdAt: 'desc' },
				skip: (page - 1) * limit,
				take: limit,
			}),
		]);

		return c.json({
			total,
			page,
			limit,
			books: books.map((book) => toBookResponse(book as BookWithRelations, (book as BookWithRelations).readingInfos?.[0] ?? null)),
		});
	} catch (error) {
		console.error(error);
		return c.json({ error: 'Failed to fetch books' }, 500);
	}
});

app.get('/:bookId/reading-info', async (c) => {
	try {
		const bookId = parseBookId(c.req.param('bookId'));
		if (!bookId) {
			return jsonError(c, 404, 'Book not found', 'BOOK_NOT_FOUND');
		}

		const result = await getAuthorizedBook(c, bookId);
		if ('response' in result) {
			return result.response;
		}

		const readingInfo = await result.prisma.readingInfo.findUnique({
			where: { userId_bookId: { userId: result.userId, bookId } },
		});
		return c.json(toReadingInfoResponse(bookId, readingInfo));
	} catch (error) {
		console.error(error);
		return c.json({ error: 'Failed to fetch reading info' }, 500);
	}
});

app.patch('/:bookId/reading-info', async (c) => {
	try {
		const bookId = parseBookId(c.req.param('bookId'));
		if (!bookId) {
			return jsonError(c, 404, 'Book not found', 'BOOK_NOT_FOUND');
		}

		const result = await getAuthorizedBook(c, bookId, true);
		if ('response' in result) {
			return result.response;
		}

		let body: unknown;
		try {
			body = await c.req.json();
		} catch {
			return jsonError(c, 400, 'Request body must be valid JSON', 'INVALID_REQUEST');
		}
		if (!isJsonObject(body)) {
			return jsonError(c, 400, 'Request body must be a JSON object', 'INVALID_REQUEST');
		}

		const input = body as { currentPage?: unknown; currentPosition?: unknown; progressPercentage?: unknown; readStatus?: unknown };
		const hasPage = input.currentPage !== undefined;
		const hasPosition = input.currentPosition !== undefined;
		if (hasPage === hasPosition) {
			return jsonError(c, 400, 'Exactly one reading position is required', 'INVALID_CURRENT_POSITION');
		}
		let currentPosition: string;
		if (hasPage) {
			if (typeof input.currentPage !== 'number' || !Number.isSafeInteger(input.currentPage) || input.currentPage < 1) {
				return jsonError(c, 400, 'currentPage must be a positive integer', 'INVALID_CURRENT_PAGE');
			}
			currentPosition = String(input.currentPage);
		} else {
			if (typeof input.currentPosition !== 'string' || !input.currentPosition.trim() || input.currentPosition.length > MAX_CURRENT_POSITION_LENGTH) {
				return jsonError(c, 400, `currentPosition must be a non-empty string of ${MAX_CURRENT_POSITION_LENGTH} characters or fewer`, 'INVALID_CURRENT_POSITION');
			}
			currentPosition = input.currentPosition;
		}
		const requestedReadStatus = input.readStatus === undefined ? 'reading' : input.readStatus;
		if (requestedReadStatus !== 'reading' && requestedReadStatus !== 'completed') {
			return jsonError(c, 400, 'readStatus must be reading or completed', 'INVALID_READ_STATUS');
		}
		const hasProgressPercentage = input.progressPercentage !== undefined;
		if (hasProgressPercentage && input.progressPercentage !== null && normalizeProgressPercentage(input.progressPercentage) === null) {
			return jsonError(c, 400, 'progressPercentage must be an integer between 0 and 100 or null', 'INVALID_PROGRESS_PERCENTAGE');
		}
		const readStatus: 'reading' | 'completed' = requestedReadStatus;
		const persistedProgress = readStatus === 'completed'
			? 100
			: hasProgressPercentage
				? normalizeProgressPercentage(input.progressPercentage)
				: undefined;

		const readingInfo = await persistReadingInfo(result.prisma, {
			userId: result.userId,
			bookId,
			currentPosition,
			readStatus,
			progressPercentage: persistedProgress,
		});
		return c.json(toReadingInfoResponse(bookId, readingInfo));
	} catch (error) {
		console.error(error);
		return c.json({ error: 'Failed to save reading info' }, 500);
	}
});

app.get('/:bookId', async (c) => {
	try {
		const bookId = parseBookId(c.req.param('bookId'));
		if (!bookId) {
			return jsonError(c, 404, 'Book not found', 'BOOK_NOT_FOUND');
		}

		const result = await getAuthorizedBook(c, bookId);
		if ('response' in result) {
			return result.response;
		}

		const readingInfo = await result.prisma.readingInfo.findUnique({
			where: { userId_bookId: { userId: result.userId, bookId } },
		});
		return c.json(toBookResponse(result.book as BookWithRelations, readingInfo));
	} catch (error) {
		console.error(error);
		return c.json({ error: 'Failed to fetch book' }, 500);
	}
});

app.get('/:bookId/file', async (c) => {
	try {
		const bookId = parseBookId(c.req.param('bookId'));
		if (!bookId) {
			return jsonError(c, 404, 'Book not found', 'BOOK_NOT_FOUND');
		}

		const result = await getAuthorizedBook(c, bookId);
		if ('response' in result) {
			return result.response;
		}

		const preferredFile = getPreferredBookFile(result.book as BookWithRelations);
		if (!preferredFile) {
			return jsonError(c, 404, 'Book file not found', 'FILE_NOT_FOUND');
		}

		const access = await getBookFileAccess(preferredFile.file.fileUrl);
		if (access.kind === 'signed-url') {
			c.header('Cache-Control', 'private, no-store');
			return c.json({ kind: 'signed-url', url: access.url, expiresAt: access.expiresAt });
		}

		const stream = Readable.toWeb(createReadStream(access.path)) as ReadableStream;
		const contentType = preferredFile.fileType === 'epub' ? 'application/epub+zip' : 'application/pdf';
		return new Response(stream, {
			headers: {
				'Content-Type': contentType,
				'Content-Disposition': `inline; filename="${encodeURIComponent(preferredFile.file.originalFileName)}"`,
				'Cache-Control': 'private, no-store',
			},
		});
	} catch (error) {
		if (error instanceof BookStorageNotFoundError) {
			return jsonError(c, 404, 'Book file not found', 'FILE_NOT_FOUND');
		}
		console.error(error);
		return c.json({ error: 'Failed to fetch book file' }, 500);
	}
});

export default app;
