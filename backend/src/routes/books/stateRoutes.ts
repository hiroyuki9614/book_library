import { Hono } from 'hono';
import type { Context } from 'hono';
import { auth } from '../../lib/auth.js';
import type { PrismaVariables } from '../../lib/prisma.js';

const app = new Hono<PrismaVariables>();
type BooksContext = Context<PrismaVariables>;
type ReadStatus = 'unread' | 'reading' | 'completed';

type ReadingInfoRecord = {
	currentPosition: string | null;
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

function jsonError(c: BooksContext, status: 400 | 401 | 403 | 404 | 500, message: string, code: string) {
	return c.json({ message, code }, status);
}

function normalizeReadStatus(value: string | undefined | null): ReadStatus {
	return value === 'reading' || value === 'completed' ? value : 'unread';
}

function toReadingInfoResponse(bookId: number, readingInfo: ReadingInfoRecord | null) {
	const currentPosition = readingInfo?.currentPosition ?? null;
	const parsedPage = currentPosition !== null && /^\d+$/.test(currentPosition) ? Number(currentPosition) : Number.NaN;
	const currentPage = Number.isSafeInteger(parsedPage) && parsedPage > 0 ? parsedPage : 1;

	return {
		bookId,
		currentPosition,
		currentPage,
		readStatus: normalizeReadStatus(readingInfo?.readStatus),
	};
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
		where: { id: userId, deletedAt: null },
		select: { id: true, roleId: true },
	});
}

function parseBookId(raw: string | undefined) {
	if (!raw || !/^[1-9]\d*$/.test(raw)) return null;
	const value = Number(raw);
	return Number.isSafeInteger(value) ? value : null;
}

async function getAuthorizedReadingContext(c: BooksContext, bookId: number) {
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

	return { prisma, userId: user.id };
}

app.get('/', async (c) => {
	try {
		const user = await getAuthenticatedUser(c);
		if (!user) return jsonError(c, 401, 'Authentication required', 'UNAUTHORIZED');

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
		const result = await getAuthorizedReadingContext(c, bookId);
		if ('response' in result) return result.response;

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
		if (!bookId) return jsonError(c, 404, 'Book not found', 'BOOK_NOT_FOUND');
		const result = await getAuthorizedReadingContext(c, bookId);
		if ('response' in result) return result.response;

		let body: unknown;
		try {
			body = await c.req.json();
		} catch {
			return jsonError(c, 400, 'Request body must be valid JSON', 'INVALID_REQUEST');
		}

		const input = body as { currentPage?: unknown; currentPosition?: unknown; readStatus?: unknown };
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
			if (typeof input.currentPosition !== 'string' || !input.currentPosition.trim() || input.currentPosition.length > 255) {
				return jsonError(c, 400, 'currentPosition must be a non-empty string of 255 characters or fewer', 'INVALID_CURRENT_POSITION');
			}
			currentPosition = input.currentPosition;
		}

		const readStatus = input.readStatus === undefined ? 'reading' : input.readStatus;
		if (readStatus !== 'reading' && readStatus !== 'completed') {
			return jsonError(c, 400, 'readStatus must be reading or completed', 'INVALID_READ_STATUS');
		}

		const readingInfo = await result.prisma.readingInfo.upsert({
			where: { userId_bookId: { userId: result.userId, bookId } },
			create: { userId: result.userId, bookId, currentPosition, readStatus },
			update: { currentPosition, readStatus },
		});
		return c.json(toReadingInfoResponse(bookId, readingInfo));
	} catch (error) {
		console.error(error);
		return c.json({ error: 'Failed to save reading info' }, 500);
	}
});

export default app;
