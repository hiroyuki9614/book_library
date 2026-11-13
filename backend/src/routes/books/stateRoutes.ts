import { Hono } from 'hono';
import type { Context } from 'hono';
import { auth } from '../../lib/auth.js';
import type { PrismaVariables } from '../../lib/prisma.js';
import { handleVersionedReadingInfoPatch, hasReadingSessionUserMismatch, MAX_CURRENT_POSITION_LENGTH, normalizeProgressPercentage, normalizeReadStatus, parseReadingSaveSequence, persistReadingInfo, READING_SESSION_USER_HEADER, StaleReadingInfoError, toReadingInfoResponse, toReadingProgress } from './readingProgress.js';

const app = new Hono<PrismaVariables>();
type BooksContext = Context<PrismaVariables>;
type ReadingInfoRecord = {
	currentPosition: string | null;
	progressPercentage?: number | null;
	readStatus: string;
};

type BookListRecord = {
	id: number;
	title: string;
	authorName: string | null;
	publishedAt: Date | null;
	publisher: string | null;
	description: string | null;
	pageTurnDirection: string;
	createdAt: Date;
	updatedAt: Date;
	category: { id: number; name: string };
	bookFiles: Array<{
		extension: string;
		mimeType: string;
		fileSize: number;
		originalFileName: string;
	}>;
	readingInfos: ReadingInfoRecord[];
};

function jsonError(c: BooksContext, status: 400 | 401 | 403 | 404 | 409 | 500, message: string, code: string) {
	return c.json({ message, code }, status);
}

function getPreferredFile(book: BookListRecord) {
	const supported = book.bookFiles.map((file) => ({
		file,
		extension: file.extension.trim().toLowerCase(),
		mimeType: file.mimeType.split(';', 1)[0].trim().toLowerCase(),
	}));
	const epub = supported.find(({ extension, mimeType }) => extension === 'epub' && mimeType === 'application/epub+zip');
	if (epub) return { file: epub.file, fileType: 'epub' as const };
	const pdf = supported.find(({ extension, mimeType }) => extension === 'pdf' && mimeType === 'application/pdf');
	if (pdf) return { file: pdf.file, fileType: 'pdf' as const };
	return null;
}

function toBookResponse(book: BookListRecord) {
	const preferred = getPreferredFile(book);
	const readingInfo = book.readingInfos[0] ?? null;
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
		hasFile: Boolean(preferred),
		fileType: preferred?.fileType ?? null,
		fileSize: preferred?.file.fileSize ?? null,
		originalFileName: preferred?.file.originalFileName ?? null,
		createdAt: book.createdAt.toISOString(),
		updatedAt: book.updatedAt.toISOString(),
	};
}

async function getAuthenticatedUser(c: BooksContext) {
	const session = await auth.api.getSession({ headers: c.req.raw.headers });
	if (!session) return null;
	const userId = Number(session.user.id);
	if (!Number.isSafeInteger(userId)) return null;

	return c.get('prisma').user.findFirst({
		where: { id: userId },
		select: { id: true, roleId: true },
	});
}

function parseBookId(raw: string | undefined) {
	if (!raw || !/^[1-9]\d*$/.test(raw)) return null;
	const value = Number(raw);
	return Number.isSafeInteger(value) ? value : null;
}

function isJsonObject(value: unknown): value is Record<string, unknown> {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
}

async function getAuthorizedReadingContext(c: BooksContext, bookId: number, requireReadingSession = false) {
	const user = await getAuthenticatedUser(c);
	if (!user) return { response: jsonError(c, 401, 'Authentication required', 'UNAUTHORIZED') };

	const prisma = c.get('prisma');
	const book = await prisma.book.findUnique({ where: { id: bookId }, select: { id: true, deletedAt: true } });
	if (!book || book.deletedAt) return { response: jsonError(c, 404, 'Book not found', 'BOOK_NOT_FOUND') };

	const permission = await prisma.roleBookPermission.findUnique({
		where: { roleId_bookId: { roleId: user.roleId, bookId } },
		select: { id: true },
	});
	if (!permission) return { response: jsonError(c, 403, 'You do not have permission to view this book', 'FORBIDDEN') };
	if (requireReadingSession && hasReadingSessionUserMismatch(c.req.header(READING_SESSION_USER_HEADER), user.id)) {
		return { response: jsonError(c, 409, 'Reading request is bound to a different authenticated user', 'READING_SESSION_MISMATCH') };
	}

	return { prisma, userId: user.id };
}

app.get('/', async (c) => {
	try {
		const user = await getAuthenticatedUser(c);
		if (!user) return jsonError(c, 401, 'Authentication required', 'UNAUTHORIZED');
		if (hasReadingSessionUserMismatch(c.req.header(READING_SESSION_USER_HEADER), user.id)) {
			return jsonError(c, 409, 'Book list request is bound to a different authenticated user', 'READING_SESSION_MISMATCH');
		}

		const page = Math.max(1, Number(c.req.query('page') ?? 1) || 1);
		const limit = Math.min(100, Math.max(1, Number(c.req.query('limit') ?? 20) || 20));
		const query = c.req.query('q')?.trim() ?? '';
		if (query.length > 100) return jsonError(c, 400, 'Search query is too long', 'INVALID_SEARCH_QUERY');

		const rawCategoryId = c.req.query('categoryId');
		const categoryId = rawCategoryId === undefined ? null : Number(rawCategoryId);
		if (rawCategoryId !== undefined && (!Number.isSafeInteger(categoryId) || (categoryId as number) < 1)) {
			return jsonError(c, 400, 'categoryId must be a positive integer', 'INVALID_CATEGORY_ID');
		}

		const where = {
			deletedAt: null,
			roleBookPermissions: { some: { roleId: user.roleId } },
			...(categoryId ? { categoryId } : {}),
			...(query
				? {
						OR: [
							{ title: { contains: query, mode: 'insensitive' as const } },
							{ authorName: { contains: query, mode: 'insensitive' as const } },
						],
					}
				: {}),
		};

		const prisma = c.get('prisma');
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

		return c.json({ total, page, limit, books: books.map((book) => toBookResponse(book as BookListRecord)) });
	} catch (error) {
		console.error(error);
		return c.json({ error: 'Failed to fetch books' }, 500);
	}
});

app.get('/:bookId/reading-info', async (c) => {
	try {
		const bookId = parseBookId(c.req.param('bookId'));
		if (!bookId) return jsonError(c, 404, 'Book not found', 'BOOK_NOT_FOUND');
		const result = await getAuthorizedReadingContext(c, bookId, true);
		if ('response' in result) return result.response;

		const readingInfo = await result.prisma.readingInfo.findUnique({
			where: { userId_bookId: { userId: result.userId, bookId } },
		});
		return c.json(toReadingInfoResponse(bookId, readingInfo, true, true));
	} catch (error) {
		console.error(error);
		return c.json({ error: 'Failed to fetch reading info' }, 500);
	}
});

app.patch('/:bookId/reading-info/cas-v1', async (c) => {
	try {
		const bookId = parseBookId(c.req.param('bookId'));
		if (!bookId) return jsonError(c, 404, 'Book not found', 'BOOK_NOT_FOUND');
		const result = await getAuthorizedReadingContext(c, bookId, true);
		if ('response' in result) return result.response;
		return await handleVersionedReadingInfoPatch(c, { bookId, userId: result.userId });
	} catch (error) {
		console.error(error);
		return c.json({ error: 'Failed to save reading info' }, 500);
	}
});

app.patch('/:bookId/reading-info', async (c) => {
	try {
		const bookId = parseBookId(c.req.param('bookId'));
		if (!bookId) return jsonError(c, 404, 'Book not found', 'BOOK_NOT_FOUND');
		const result = await getAuthorizedReadingContext(c, bookId, true);
		if ('response' in result) return result.response;

		let body: unknown;
		try {
			body = await c.req.json();
		} catch {
			return jsonError(c, 400, 'Request body must be valid JSON', 'INVALID_REQUEST');
		}
		if (!isJsonObject(body)) {
			return jsonError(c, 400, 'Request body must be a JSON object', 'INVALID_REQUEST');
		}

		const input = body as { currentPage?: unknown; currentPosition?: unknown; progressPercentage?: unknown; readStatus?: unknown; saveSequence?: unknown; expectedSaveSequence?: unknown };
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
		const saveSequence = parseReadingSaveSequence(input.saveSequence);
		const expectedSaveSequence = parseReadingSaveSequence(input.expectedSaveSequence);
		if (saveSequence === null) {
			return jsonError(c, 400, 'saveSequence must be a safe non-negative integer', 'INVALID_SAVE_SEQUENCE');
		}
		if (expectedSaveSequence === null) {
			return jsonError(c, 400, 'expectedSaveSequence must be a safe non-negative integer', 'INVALID_EXPECTED_SAVE_SEQUENCE');
		}
		if (saveSequence !== undefined && saveSequence > 0n && expectedSaveSequence === undefined) {
			return jsonError(c, 400, 'Versioned saves require expectedSaveSequence', 'EXPECTED_SAVE_SEQUENCE_REQUIRED');
		}
		if (expectedSaveSequence !== undefined && (saveSequence === undefined || saveSequence <= expectedSaveSequence)) {
			return jsonError(c, 400, 'saveSequence must be greater than expectedSaveSequence', 'INVALID_SAVE_SEQUENCE');
		}
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
			saveSequence,
			expectedSaveSequence,
		});
		return c.json(toReadingInfoResponse(bookId, readingInfo, true));
	} catch (error) {
		if (error instanceof StaleReadingInfoError) {
			return jsonError(c, 409, error.message, error.code);
		}
		console.error(error);
		return c.json({ error: 'Failed to save reading info' }, 500);
	}
});

export default app;
