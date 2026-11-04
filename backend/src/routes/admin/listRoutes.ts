import { Hono } from 'hono';
import type { Context } from 'hono';
import { BookStorageNotFoundError, getBookFileAccess } from '../../lib/bookStorage.js';
import { auth } from '../../lib/auth.js';
import type { PrismaVariables } from '../../lib/prisma.js';

const app = new Hono<PrismaVariables>();
type AdminContext = Context<PrismaVariables>;

type PublicationScope = 'all_users' | 'admin_only';
type AdminBookState = 'active' | 'deleted';

type AdminBookRecord = {
	id: number;
	title: string;
	authorName: string | null;
	publisher: string | null;
	publishedAt: Date | null;
	categoryId: number;
	pageTurnDirection: string;
	description: string | null;
	deletedAt: Date | null;
	category: { id: number; name: string };
	bookFiles: Array<{
		id: number;
		extension: string;
		mimeType: string;
		originalFileName: string;
		fileSize: number;
		fileUrl: string;
	}>;
	roleBookPermissions: Array<{ role: { name: string } }>;
};

function jsonError(c: AdminContext, status: 400 | 401 | 403 | 404 | 409 | 500, message: string, code: string) {
	return c.json({ message, code }, status);
}

async function requireAdmin(c: AdminContext) {
	const session = await auth.api.getSession({ headers: c.req.raw.headers });
	if (!session) return { response: jsonError(c, 401, 'Authentication required', 'UNAUTHORIZED') };
	const userId = Number(session.user.id);
	if (!Number.isSafeInteger(userId)) return { response: jsonError(c, 401, 'Authentication required', 'UNAUTHORIZED') };

	const user = await c.get('prisma').user.findFirst({
		where: { id: userId, deletedAt: null },
		select: { id: true, role: { select: { name: true } } },
	});
	if (!user || user.role.name !== 'admin') {
		return { response: jsonError(c, 403, 'Administrator role required', 'FORBIDDEN') };
	}
	return { user };
}

function parseBookId(raw: string | undefined) {
	if (!raw || !/^[1-9]\d*$/.test(raw)) return null;
	const bookId = Number(raw);
	return Number.isSafeInteger(bookId) ? bookId : null;
}

function publicationScopeOf(book: AdminBookRecord): PublicationScope {
	return book.roleBookPermissions.some(({ role }) => role.name === 'user') ? 'all_users' : 'admin_only';
}

function toAdminBook(book: AdminBookRecord) {
	const first = book.bookFiles[0];
	const file = first ? { id: first.id, extension: first.extension, mimeType: first.mimeType, originalFileName: first.originalFileName, fileSize: first.fileSize } : null;
	return {
		id: book.id,
		title: book.title,
		authorName: book.authorName,
		publisher: book.publisher,
		publishedAt: book.publishedAt?.toISOString() ?? null,
		categoryId: book.categoryId,
		pageTurnDirection: book.pageTurnDirection,
		description: book.description,
		deletedAt: book.deletedAt?.toISOString() ?? null,
		category: book.category,
		publicationScope: publicationScopeOf(book),
		file,
	};
}

app.get('/books', async (c) => {
	try {
		const admin = await requireAdmin(c);
		if ('response' in admin) return admin.response;

		const rawState = c.req.query('state') ?? 'active';
		if (rawState !== 'active' && rawState !== 'deleted') {
			return jsonError(c, 400, 'state must be active or deleted', 'INVALID_BOOK_STATE');
		}
		const state: AdminBookState = rawState;

		const books = await c.get('prisma').book.findMany({
			where: state === 'deleted' ? { deletedAt: { not: null } } : { deletedAt: null },
			orderBy: { createdAt: 'desc' },
			include: {
				category: { select: { id: true, name: true } },
				bookFiles: {
					orderBy: { id: 'asc' },
					select: { id: true, extension: true, mimeType: true, originalFileName: true, fileSize: true, fileUrl: true },
				},
				roleBookPermissions: { select: { role: { select: { name: true } } } },
			},
		});

		const results = await Promise.all(books.map(async (book) => {
   const response = toAdminBook(book as AdminBookRecord);
   if (state !== 'deleted') return response;
   const checks = await Promise.all(book.bookFiles.map(async (file) => {
    try { await getBookFileAccess(file.fileUrl); return 'available'; }
    catch (error) { return error instanceof BookStorageNotFoundError ? 'missing' : 'unknown'; }
   }));
   return { ...response, fileMissing: checks.includes('missing'), fileCheckFailed: checks.includes('unknown') };
  }));
  return c.json({ books: results });
	} catch (error) {
		console.error(error);
		return c.json({ error: 'Failed to load admin books' }, 500);
	}
});

app.patch('/books/:bookId/delete', async (c) => {
	try {
		const admin = await requireAdmin(c);
		if ('response' in admin) return admin.response;

		const bookId = parseBookId(c.req.param('bookId'));
		if (!bookId) return jsonError(c, 404, 'Book not found', 'BOOK_NOT_FOUND');

		const prisma = c.get('prisma');
		const current = await prisma.book.findUnique({
			where: { id: bookId },
			select: { id: true, deletedAt: true },
		});
		if (!current) return jsonError(c, 404, 'Book not found', 'BOOK_NOT_FOUND');
		if (current.deletedAt) {
			return c.json({ id: current.id, deletedAt: current.deletedAt.toISOString() });
		}

		const deletedAt = new Date();
		const book = await prisma.book.update({
			where: { id: bookId },
			data: { deletedAt },
			select: { id: true, deletedAt: true },
		});
		return c.json({ id: book.id, deletedAt: book.deletedAt?.toISOString() ?? deletedAt.toISOString() });
	} catch (error) {
		console.error(error);
		return c.json({ error: 'Failed to delete book' }, 500);
	}
});

app.patch('/books/:bookId/restore', async (c) => {
	try {
		const admin = await requireAdmin(c);
		if ('response' in admin) return admin.response;

		const bookId = parseBookId(c.req.param('bookId'));
		if (!bookId) return jsonError(c, 404, 'Book not found', 'BOOK_NOT_FOUND');

		const prisma = c.get('prisma');
  const result = await prisma.$transaction(async (tx) => {
   await tx.$queryRawUnsafe('SELECT id FROM books WHERE id = $1 FOR UPDATE', bookId);
   const current = await tx.book.findUnique({
    where: { id: bookId }, include: { bookFiles: true },
   });
   if (!current) return { state: 'missing' as const };
   if (!current.deletedAt) return { state: 'ok' as const, book: current };
   for (const file of current.bookFiles) {
    try { await getBookFileAccess(file.fileUrl); }
    catch { return { state: 'files_unavailable' as const }; }
   }
   const book = await tx.book.update({
    where: { id: bookId }, data: { deletedAt: null }, select: { id: true, deletedAt: true },
   });
   return { state: 'ok' as const, book };
  }, { timeout: 30_000, maxWait: 5_000 });
  if (result.state === 'missing') return jsonError(c, 404, 'Book not found', 'BOOK_NOT_FOUND');
  if (result.state === 'files_unavailable') return jsonError(c, 409, 'Book files are missing or unavailable; retry permanent deletion', 'BOOK_FILES_UNAVAILABLE');
  return c.json({ id: result.book.id, deletedAt: null });
	} catch (error) {
		console.error(error);
		return c.json({ error: 'Failed to restore book' }, 500);
	}
});

export default app;
