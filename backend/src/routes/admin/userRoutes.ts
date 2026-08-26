import { hashPassword } from 'better-auth/crypto';
import { Hono } from 'hono';
import type { Context } from 'hono';
import { auth } from '../../lib/auth.js';
import type { PrismaVariables } from '../../lib/prisma.js';

const app = new Hono<PrismaVariables>();
type AdminContext = Context<PrismaVariables>;

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

function parseUserId(raw: string | undefined) {
	if (!raw || !/^[1-9]\d*$/.test(raw)) return null;
	const id = Number(raw);
	return Number.isSafeInteger(id) ? id : null;
}

function parseNewUser(body: unknown) {
	if (!body || typeof body !== 'object' || Array.isArray(body)) return null;
	const input = body as Record<string, unknown>;
	if (Object.keys(input).some((key) => !['email', 'name', 'password'].includes(key))) return null;
	if (typeof input.email !== 'string' || typeof input.name !== 'string' || typeof input.password !== 'string') return null;

	const email = input.email.trim().toLowerCase();
	const name = input.name.trim();
	const password = input.password;
	if (!email || email.length > 254 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return null;
	if (!name || name.length > 255) return null;
	if (password.length < 8) return null;
	return { email, name, password };
}

function parsePassword(body: unknown) {
	if (!body || typeof body !== 'object' || Array.isArray(body)) return null;
	const input = body as Record<string, unknown>;
	if (Object.keys(input).some((key) => key !== 'password')) return null;
	return typeof input.password === 'string' && input.password.length >= 8 ? input.password : null;
}

async function findGeneralUser(c: AdminContext, userId: number) {
	return c.get('prisma').user.findFirst({
		where: { id: userId, role: { name: 'user' } },
		select: { id: true, email: true, name: true, deletedAt: true },
	});
}

app.get('/users', async (c) => {
	try {
		const admin = await requireAdmin(c);
		if ('response' in admin) return admin.response;

		const users = await c.get('prisma').user.findMany({
			where: { role: { name: 'user' } },
			orderBy: [{ deletedAt: 'asc' }, { createdAt: 'desc' }],
			select: { id: true, email: true, name: true, deletedAt: true, createdAt: true },
		});
		return c.json({
			users: users.map((user) => ({
				...user,
				deletedAt: user.deletedAt?.toISOString() ?? null,
				createdAt: user.createdAt.toISOString(),
			})),
		});
	} catch (error) {
		console.error(error);
		return c.json({ error: 'Failed to load users' }, 500);
	}
});

app.post('/users', async (c) => {
	try {
		const admin = await requireAdmin(c);
		if ('response' in admin) return admin.response;

		let body: unknown;
		try {
			body = await c.req.json();
		} catch {
			return jsonError(c, 400, 'Request body must be valid JSON', 'INVALID_REQUEST');
		}
		const input = parseNewUser(body);
		if (!input) return jsonError(c, 400, 'User data is invalid', 'INVALID_USER');

		const prisma = c.get('prisma');
		const existing = await prisma.user.findFirst({
			where: { email: { equals: input.email, mode: 'insensitive' } },
			select: { id: true },
		});
		if (existing) return jsonError(c, 409, 'Email address is already registered', 'DUPLICATE_EMAIL');

		const role = await prisma.role.findUnique({ where: { name: 'user' }, select: { id: true } });
		if (!role) return jsonError(c, 500, 'General user role is not available', 'USER_ROLE_MISSING');
		const passwordHash = await hashPassword(input.password);

		const user = await prisma.$transaction(async (tx) => {
			const created = await tx.user.create({
				data: { email: input.email, name: input.name, roleId: role.id },
				select: { id: true, email: true, name: true, deletedAt: true, createdAt: true },
			});
			await tx.account.create({
				data: {
					accountId: String(created.id),
					providerId: 'credential',
					userId: created.id,
					password: passwordHash,
				},
			});
			return created;
		});

		return c.json({
			...user,
			deletedAt: null,
			createdAt: user.createdAt.toISOString(),
		}, 201);
	} catch (error) {
		console.error(error);
		return c.json({ error: 'Failed to create user' }, 500);
	}
});

app.patch('/users/:userId/disable', async (c) => {
	try {
		const admin = await requireAdmin(c);
		if ('response' in admin) return admin.response;
		const userId = parseUserId(c.req.param('userId'));
		if (!userId) return jsonError(c, 404, 'User not found', 'USER_NOT_FOUND');
		const current = await findGeneralUser(c, userId);
		if (!current) return jsonError(c, 404, 'User not found', 'USER_NOT_FOUND');

		const user = current.deletedAt
			? current
			: await c.get('prisma').user.update({
					where: { id: userId },
					data: { deletedAt: new Date() },
					select: { id: true, email: true, name: true, deletedAt: true },
				});
		return c.json({ ...user, deletedAt: user.deletedAt?.toISOString() ?? null });
	} catch (error) {
		console.error(error);
		return c.json({ error: 'Failed to disable user' }, 500);
	}
});

app.patch('/users/:userId/restore', async (c) => {
	try {
		const admin = await requireAdmin(c);
		if ('response' in admin) return admin.response;
		const userId = parseUserId(c.req.param('userId'));
		if (!userId) return jsonError(c, 404, 'User not found', 'USER_NOT_FOUND');
		const current = await findGeneralUser(c, userId);
		if (!current) return jsonError(c, 404, 'User not found', 'USER_NOT_FOUND');

		const user = current.deletedAt
			? await c.get('prisma').user.update({
					where: { id: userId },
					data: { deletedAt: null },
					select: { id: true, email: true, name: true, deletedAt: true },
				})
			: current;
		return c.json({ ...user, deletedAt: user.deletedAt?.toISOString() ?? null });
	} catch (error) {
		console.error(error);
		return c.json({ error: 'Failed to restore user' }, 500);
	}
});

app.patch('/users/:userId/password', async (c) => {
	try {
		const admin = await requireAdmin(c);
		if ('response' in admin) return admin.response;
		const userId = parseUserId(c.req.param('userId'));
		if (!userId) return jsonError(c, 404, 'User not found', 'USER_NOT_FOUND');
		const current = await findGeneralUser(c, userId);
		if (!current) return jsonError(c, 404, 'User not found', 'USER_NOT_FOUND');

		let body: unknown;
		try {
			body = await c.req.json();
		} catch {
			return jsonError(c, 400, 'Request body must be valid JSON', 'INVALID_REQUEST');
		}
		const password = parsePassword(body);
		if (!password) return jsonError(c, 400, 'Password must be at least 8 characters', 'INVALID_PASSWORD');

		const account = await c.get('prisma').account.findFirst({
			where: { userId, providerId: 'credential' },
			select: { id: true },
		});
		if (!account) return jsonError(c, 500, 'Credential account is not available', 'CREDENTIAL_ACCOUNT_MISSING');

		await c.get('prisma').account.update({
			where: { id: account.id },
			data: { password: await hashPassword(password) },
		});
		return c.json({ id: userId, passwordReset: true });
	} catch (error) {
		console.error(error);
		return c.json({ error: 'Failed to reset password' }, 500);
	}
});

export default app;
