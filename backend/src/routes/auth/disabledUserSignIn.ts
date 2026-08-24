import { Hono } from 'hono';
import { auth } from '../../lib/auth.js';
import { prisma } from '../../lib/prisma.js';

const app = new Hono();

type EmailSignInBody = {
	email?: unknown;
};

app.post('/sign-in/email', async (c) => {
	let body: EmailSignInBody | null = null;
	try {
		body = (await c.req.raw.clone().json()) as EmailSignInBody;
	} catch {
		return auth.handler(c.req.raw);
	}

	const email = typeof body?.email === 'string' ? body.email.trim() : '';
	if (!email) {
		return auth.handler(c.req.raw);
	}

	const disabledUser = await prisma.user.findFirst({
		where: {
			email: { equals: email, mode: 'insensitive' },
			deletedAt: { not: null },
		},
		select: { id: true },
	});

	if (disabledUser) {
		return c.json(
			{
				code: 'INVALID_EMAIL_OR_PASSWORD',
				message: 'Invalid email or password',
			},
			401,
		);
	}

	return auth.handler(c.req.raw);
});

export default app;
