import { Hono } from 'hono';
import type { Context } from 'hono';
import { auth } from '../../lib/auth.js';
import type { PrismaVariables } from '../../lib/prisma.js';

const app = new Hono<PrismaVariables>();
type AdminContext = Context<PrismaVariables>;

type PublicationScope = 'all_users' | 'admin_only';

type AdminBookRecord = {
	id: number;
	title: string;
	authorName: string | null;
	publisher: string | null;
	publishedAt: Date | null;
	categoryId: number;
	pageTurnDirection: string;
	description: string | null;
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

function jsonError(c: AdminContext, status: 401 | 403 | 500, message: string, code: string) {
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
		category: book.category,
		publicationScope: publicationScopeOf(book),
		file,
	};
}

app.get('/books', async (c) => {
	try {
		const admin = await requireAdmin(c);
		if ('response' in admin) return admin.response;

		const books = await c.get('prisma').book.findMany({
			where: { deletedAt: null },
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

export default app;
