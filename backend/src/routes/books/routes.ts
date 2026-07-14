import { Hono } from 'hono';
import { PrismaPg } from '@prisma/adapter-pg';
import { PrismaClient } from '@prisma/client';
import { Pool } from 'pg';
import type { PrismaVariables } from '../../lib/prisma.js';

const app = new Hono<PrismaVariables>();

app.get('/', async (c) => {
	try {
		const prisma = c.get('prisma');
		const books = await prisma.book.findMany();

		console.log('Fetched books:', books);
		return c.json(books);
	} catch (error) {
		console.error(error);
		return c.json({ error: 'Failed to fetch books' }, 500);
	}
});

export default app;
