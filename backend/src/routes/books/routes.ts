import { Hono } from 'hono';
import type { Context } from 'hono';
import { createReadStream } from 'node:fs';
import { Readable } from 'node:stream';
import { auth } from '../../lib/auth.js';
import { BookStorageNotFoundError, getBookFileAccess } from '../../lib/bookStorage.js';
import type { PrismaVariables } from '../../lib/prisma.js';

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
};

type BookFileRecord = BookWithRelations['bookFiles'][number];
type SupportedBookFileType = 'epub' | 'pdf';

function jsonError(c: BooksContext, status: 400 | 401 | 403 | 404 | 500, message: string, code: string) {
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

type ReadingInfoRecord = {
	currentPosition: string | null;
	readStatus: string;
};

function toReadingInfoResponse(bookId: number, readingInfo: ReadingInfoRecord | null) {
	const parsedPage = Number(readingInfo?.currentPosition);
	const currentPage = Number.isSafeInteger(parsedPage) && parsedPage > 0 ? parsedPage : 1;
	const readStatus = ['unread', 'reading', 'completed'].includes(readingInfo?.readStatus ?? '')
		? readingInfo?.readStatus
		: 'unread';

	return { bookId, currentPage, readStatus };
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

function toBookResponse(book: BookWithRelations, readStatus = 'unread') {
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
		readStatus,
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

async function getAuthorizedBook(c: BooksContext, bookId: number) {
	const user = await getAuthenticatedUser(c);
	if (!user) {
		return { response: jsonError(c, 401, 'Authentication required', 'UNAUTHORIZED') };
	}

	return getBookForAuthorizedUser(c, bookId, user.id, user.roleId);
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
				include: { category: true, bookFiles: { orderBy: { id: 'asc' } } },
				orderBy: { createdAt: 'desc' },
				skip: (page - 1) * limit,
				take: limit,
			}),
		]);

		return c.json({
			total,
			page,
			limit,
			books: books.map((book) => toBookResponse(book as BookWithRelations)),
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

		const result = await getAuthorizedBook(c, bookId);
		if ('response' in result) {
			return result.response;
		}

		let body: unknown;
		try {
			body = await c.req.json();
		} catch {
			return jsonError(c, 400, 'Request body must be valid JSON', 'INVALID_REQUEST');
		}

		const currentPage = (body as { currentPage?: unknown })?.currentPage;
		if (typeof currentPage !== 'number' || !Number.isSafeInteger(currentPage) || currentPage < 1) {
			return jsonError(c, 400, 'currentPage must be a positive integer', 'INVALID_CURRENT_PAGE');
		}

		const readingInfo = await result.prisma.readingInfo.upsert({
			where: { userId_bookId: { userId: result.userId, bookId } },
			create: { userId: result.userId, bookId, currentPosition: String(currentPage), readStatus: 'reading' },
			update: { currentPosition: String(currentPage), readStatus: 'reading' },
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
		return c.json(toBookResponse(result.book as BookWithRelations, readingInfo?.readStatus ?? 'unread'));
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
		if (access.kind === 'redirect') {
			return new Response(null, {
				status: 302,
				headers: {
					Location: access.url,
					'Cache-Control': 'private, no-store',
					'X-BeLib-File-Url-Expires-At': access.expiresAt,
				},
			});
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
