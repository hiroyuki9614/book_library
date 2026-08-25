import { Hono } from 'hono';
import type { Context } from 'hono';
import { auth } from '../../lib/auth.js';
import type { PrismaVariables } from '../../lib/prisma.js';

const app = new Hono<PrismaVariables>();
type AdminContext = Context<PrismaVariables>;
type PublicationScope = 'all_users' | 'admin_only';

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
	const id = Number(raw);
	return Number.isSafeInteger(id) ? id : null;
}

function normalizeOptionalString(value: unknown, maxLength?: number) {
	if (value === undefined || value === null || value === '') return null;
	if (typeof value !== 'string') return undefined;
	const normalized = value.trim();
	if (maxLength !== undefined && normalized.length > maxLength) return undefined;
	return normalized || null;
}

function parsePublishedAt(value: unknown): Date | null | undefined {
	if (value === undefined || value === null || value === '') return null;
	if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return undefined;
	const parsed = new Date(`${value}T00:00:00.000Z`);
	const [year, month, day] = value.split('-').map(Number);
	if (
		!Number.isFinite(parsed.getTime()) ||
		parsed.getUTCFullYear() !== year ||
		parsed.getUTCMonth() + 1 !== month ||
		parsed.getUTCDate() !== day
	) return undefined;
	return parsed;
}

function parseMetadata(body: unknown): ParsedBookMetadata | null {
	if (!body || typeof body !== 'object' || Array.isArray(body)) return null;
	const input = body as Record<string, unknown>;
	const allowed = new Set([
		'title', 'authorName', 'publisher', 'publishedAt', 'categoryId',
		'pageTurnDirection', 'description', 'publicationScope',
	]);
	if (Object.keys(input).some((key) => !allowed.has(key))) return null;
	if (typeof input.title !== 'string' || !input.title.trim() || input.title.trim().length > 255) return null;
	if (typeof input.categoryId !== 'number' || !Number.isSafeInteger(input.categoryId) || input.categoryId < 1) return null;
	if (input.pageTurnDirection !== 'ltr' && input.pageTurnDirection !== 'rtl') return null;
	if (input.publicationScope !== 'all_users' && input.publicationScope !== 'admin_only') return null;

	const authorName = normalizeOptionalString(input.authorName, 255);
	const publisher = normalizeOptionalString(input.publisher, 255);
	const description = normalizeOptionalString(input.description);
	const publishedAt = parsePublishedAt(input.publishedAt);
	if (authorName === undefined || publisher === undefined || description === undefined || publishedAt === undefined) return null;

	return {
		title: input.title.trim(),
		authorName,
		publisher,
		publishedAt,
		categoryId: input.categoryId,
		pageTurnDirection: input.pageTurnDirection,
		description,
		publicationScope: input.publicationScope,
	};
}

function permissionCreatesForScope(scope: PublicationScope) {
	const roleNames = scope === 'all_users' ? ['admin', 'user'] : ['admin'];
	return roleNames.map((name) => ({ role: { connect: { name } } }));
}

app.patch('/books/:bookId', async (c) => {
	try {
		const admin = await requireAdmin(c);
		if ('response' in admin) return admin.response;

		const bookId = parseBookId(c.req.param('bookId'));
		if (!bookId) return jsonError(c, 404, 'Book not found', 'BOOK_NOT_FOUND');

		let body: unknown;
		try {
			body = await c.req.json();
		} catch {
			return jsonError(c, 400, 'Request body must be valid JSON', 'INVALID_REQUEST');
		}
		const input = parseMetadata(body);
		if (!input) return jsonError(c, 400, 'Book metadata is invalid', 'INVALID_BOOK');

		const prisma = c.get('prisma');
		const current = await prisma.book.findUnique({
			where: { id: bookId },
			select: { id: true, deletedAt: true },
		});
		if (!current) return jsonError(c, 404, 'Book not found', 'BOOK_NOT_FOUND');
		if (current.deletedAt) return jsonError(c, 409, 'Deleted books cannot be edited', 'BOOK_DELETED');

		const category = await prisma.category.findUnique({
			where: { id: input.categoryId },
			select: { id: true, isActive: true },
		});
		if (!category?.isActive) return jsonError(c, 400, 'Active category is required', 'INVALID_CATEGORY');

		const book = await prisma.book.update({
			where: { id: bookId },
			data: {
				title: input.title,
				authorName: input.authorName,
				publisher: input.publisher,
				publishedAt: input.publishedAt,
				pageTurnDirection: input.pageTurnDirection,
				description: input.description,
				category: { connect: { id: input.categoryId } },
				roleBookPermissions: {
					deleteMany: {},
					create: permissionCreatesForScope(input.publicationScope),
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
				deletedAt: true,
				category: { select: { id: true, name: true } },
			},
		});

		return c.json({
			...book,
			publishedAt: book.publishedAt?.toISOString() ?? null,
			deletedAt: book.deletedAt?.toISOString() ?? null,
			publicationScope: input.publicationScope,
		});
	} catch (error) {
		console.error(error);
		return c.json({ error: 'Failed to update book metadata' }, 500);
	}
});

export default app;
