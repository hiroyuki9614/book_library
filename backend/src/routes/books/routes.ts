import { Hono } from 'hono';
import type { Context } from 'hono';
import { createReadStream } from 'node:fs';
import { access, realpath } from 'node:fs/promises';
import { Readable } from 'node:stream';
import { resolve, relative, isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';
import { auth } from '../../lib/auth.js';
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
		where: { id: userId, deletedAt: null },
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

function toBookResponse(book: BookWithRelations, readStatus = 'unread') {
	const file = book.bookFiles[0];
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
		fileType: file?.extension.toLowerCase() ?? null,
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

function isWithinRoot(root: string, candidate: string) {
	const relativePath = relative(root, candidate);
	return relativePath === '' || (!relativePath.startsWith('..') && !isAbsolute(relativePath));
}

async function resolveBookFilePath(fileUrl: string) {
	const storageRoot = resolve(process.env.BOOK_FILE_STORAGE_ROOT ?? resolve(process.cwd(), 'storage'));
	const rootPath = await realpath(storageRoot);

	let requestedPath: string;
	if (fileUrl.startsWith('file://')) {
		const parsedUrl = new URL(fileUrl);
		if (parsedUrl.hostname) {
			throw new Error('Remote file URLs are not allowed');
		}
		requestedPath = fileURLToPath(parsedUrl);
	} else {
		if (!fileUrl || fileUrl.includes('\0') || isAbsolute(fileUrl) || /^[a-z][a-z\d+.-]*:/i.test(fileUrl)) {
			throw new Error('Only relative local file keys are allowed');
		}
		requestedPath = resolve(rootPath, fileUrl);
	}

	if (!isWithinRoot(rootPath, requestedPath)) {
		throw new Error('File path escapes storage root');
	}

	const resolvedPath = await realpath(requestedPath);
	if (!isWithinRoot(rootPath, resolvedPath)) {
		throw new Error('Resolved file path escapes storage root');
	}

	await access(resolvedPath);
	return resolvedPath;
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

		const pdfFile = (result.book as BookWithRelations).bookFiles.find(
			(file) => file.extension.toLowerCase() === 'pdf' && file.mimeType.toLowerCase() === 'application/pdf',
		);
		if (!pdfFile) {
			return jsonError(c, 404, 'PDF file not found', 'FILE_NOT_FOUND');
		}

		const filePath = await resolveBookFilePath(pdfFile.fileUrl);
		const stream = Readable.toWeb(createReadStream(filePath)) as ReadableStream;
		return new Response(stream, {
			headers: {
				'Content-Type': 'application/pdf',
				'Content-Disposition': `inline; filename="${encodeURIComponent(pdfFile.originalFileName)}"`,
				'Cache-Control': 'private, no-store',
			},
		});
	} catch (error) {
		if (error instanceof Error && /ENOENT|not found|escapes|allowed|storage/i.test(error.message)) {
			return jsonError(c, 404, 'PDF file not found', 'FILE_NOT_FOUND');
		}
		console.error(error);
		return c.json({ error: 'Failed to fetch book file' }, 500);
	}
});

export default app;
