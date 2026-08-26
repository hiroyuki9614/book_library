import { Hono } from 'hono';
import type { Context } from 'hono';
import { auth } from '../../lib/auth.js';
import type { PrismaVariables } from '../../lib/prisma.js';

const app = new Hono<PrismaVariables>();
type AdminContext = Context<PrismaVariables>;

const UNCATEGORIZED_NAME = '未分類';

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

function parseCategoryId(raw: string | undefined) {
	if (!raw || !/^[1-9]\d*$/.test(raw)) return null;
	const id = Number(raw);
	return Number.isSafeInteger(id) ? id : null;
}

function parseCategoryName(body: unknown) {
	if (!body || typeof body !== 'object' || Array.isArray(body)) return null;
	const name = (body as { name?: unknown }).name;
	if (typeof name !== 'string') return null;
	const normalized = name.trim();
	if (!normalized || normalized.length > 255) return null;
	return normalized;
}

function isUniqueConstraintError(error: unknown) {
	return Boolean(error && typeof error === 'object' && 'code' in error && (error as { code?: unknown }).code === 'P2002');
}

app.get('/categories', async (c) => {
	try {
		const admin = await requireAdmin(c);
		if ('response' in admin) return admin.response;

		const categories = await c.get('prisma').category.findMany({
			where: { isActive: true },
			orderBy: [{ displayOrder: 'asc' }, { id: 'asc' }],
			select: { id: true, name: true, displayOrder: true, isActive: true },
		});
		return c.json({ categories });
	} catch (error) {
		console.error(error);
		return c.json({ error: 'Failed to load categories' }, 500);
	}
});

app.post('/categories', async (c) => {
	try {
		const admin = await requireAdmin(c);
		if ('response' in admin) return admin.response;

		let body: unknown;
		try {
			body = await c.req.json();
		} catch {
			return jsonError(c, 400, 'Request body must be valid JSON', 'INVALID_REQUEST');
		}
		const name = parseCategoryName(body);
		if (!name || name === UNCATEGORIZED_NAME) {
			return jsonError(c, 400, 'Category name is invalid', 'INVALID_CATEGORY_NAME');
		}

		const prisma = c.get('prisma');
		const lastCategory = await prisma.category.findFirst({
			where: { isActive: true },
			orderBy: [{ displayOrder: 'desc' }, { id: 'desc' }],
			select: { displayOrder: true },
		});
		const category = await prisma.category.create({
			data: { name, displayOrder: (lastCategory?.displayOrder ?? 0) + 1, isActive: true },
			select: { id: true, name: true, displayOrder: true, isActive: true },
		});
		return c.json(category, 201);
	} catch (error) {
		if (isUniqueConstraintError(error)) {
			return jsonError(c, 409, 'A category with the same name already exists', 'DUPLICATE_CATEGORY');
		}
		console.error(error);
		return c.json({ error: 'Failed to create category' }, 500);
	}
});

app.patch('/categories/:categoryId', async (c) => {
	try {
		const admin = await requireAdmin(c);
		if ('response' in admin) return admin.response;
		const categoryId = parseCategoryId(c.req.param('categoryId'));
		if (!categoryId) return jsonError(c, 404, 'Category not found', 'CATEGORY_NOT_FOUND');

		let body: unknown;
		try {
			body = await c.req.json();
		} catch {
			return jsonError(c, 400, 'Request body must be valid JSON', 'INVALID_REQUEST');
		}
		const name = parseCategoryName(body);
		if (!name || name === UNCATEGORIZED_NAME) {
			return jsonError(c, 400, 'Category name is invalid', 'INVALID_CATEGORY_NAME');
		}

		const prisma = c.get('prisma');
		const current = await prisma.category.findUnique({
			where: { id: categoryId },
			select: { id: true, name: true, isActive: true },
		});
		if (!current || !current.isActive) return jsonError(c, 404, 'Category not found', 'CATEGORY_NOT_FOUND');
		if (current.name === UNCATEGORIZED_NAME) {
			return jsonError(c, 409, 'The uncategorized category cannot be renamed', 'PROTECTED_CATEGORY');
		}

		const category = await prisma.category.update({
			where: { id: categoryId },
			data: { name },
			select: { id: true, name: true, displayOrder: true, isActive: true },
		});
		return c.json(category);
	} catch (error) {
		if (isUniqueConstraintError(error)) {
			return jsonError(c, 409, 'A category with the same name already exists', 'DUPLICATE_CATEGORY');
		}
		console.error(error);
		return c.json({ error: 'Failed to update category' }, 500);
	}
});

app.patch('/categories/:categoryId/delete', async (c) => {
	try {
		const admin = await requireAdmin(c);
		if ('response' in admin) return admin.response;
		const categoryId = parseCategoryId(c.req.param('categoryId'));
		if (!categoryId) return jsonError(c, 404, 'Category not found', 'CATEGORY_NOT_FOUND');

		const prisma = c.get('prisma');
		const category = await prisma.category.findUnique({
			where: { id: categoryId },
			select: { id: true, name: true, isActive: true },
		});
		if (!category || !category.isActive) return jsonError(c, 404, 'Category not found', 'CATEGORY_NOT_FOUND');
		if (category.name === UNCATEGORIZED_NAME) {
			return jsonError(c, 409, 'The uncategorized category cannot be deleted', 'PROTECTED_CATEGORY');
		}

		const uncategorized = await prisma.category.findUnique({
			where: { name: UNCATEGORIZED_NAME },
			select: { id: true, isActive: true },
		});
		if (!uncategorized || !uncategorized.isActive) {
			return jsonError(c, 500, 'The uncategorized category is not available', 'UNCATEGORIZED_CATEGORY_MISSING');
		}

		const result = await prisma.$transaction(async (tx) => {
			const moved = await tx.book.updateMany({
				where: { categoryId },
				data: { categoryId: uncategorized.id },
			});
			const deleted = await tx.category.update({
				where: { id: categoryId },
				data: { isActive: false },
				select: { id: true, name: true, isActive: true },
			});
			return { movedBookCount: moved.count, category: deleted };
		});
		return c.json(result);
	} catch (error) {
		console.error(error);
		return c.json({ error: 'Failed to delete category' }, 500);
	}
});

export default app;
