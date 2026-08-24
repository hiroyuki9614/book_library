import { Hono } from 'hono';
import type { Context } from 'hono';
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
	}>;
	roleBookPermissions: Array<{ role: { name: string } }>;
};

function jsonError(c: AdminContext, status: 400 | 401 | 403 | 404 | 500, message: string, code: string) {
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
	const file = book.bookFiles[0] ?? null;
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
					take: 1,
					select: { id: true, extension: true, mimeType: true, originalFileName: true, fileSize: true },
				},
				roleBookPermissions: { select: { role: { select: { name: true } } } },
			},
		});

		return c.json({ books: books.map((book) => toAdminBook(book as AdminBookRecord)) });
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
		const current = await prisma.book.findUnique({
			where: { id: bookId },
			select: { id: true, deletedAt: true },
		});
		if (!current) return jsonError(c, 404, 'Book not found', 'BOOK_NOT_FOUND');
		if (!current.deletedAt) return c.json({ id: current.id, deletedAt: null });

		const book = await prisma.book.update({
			where: { id: bookId },
			data: { deletedAt: null },
			select: { id: true, deletedAt: true },
		});
		return c.json({ id: book.id, deletedAt: book.deletedAt?.toISOString() ?? null });
	} catch (error) {
		console.error(error);
		return c.json({ error: 'Failed to restore book' }, 500);
	}
});

export default app;
