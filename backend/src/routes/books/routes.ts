import { Hono } from 'hono';
import withPrisma from '../../lib/prisma.js';
import type { PrismaVariables } from '../../lib/prisma.js';

const app = new Hono<PrismaVariables>();

app.use('*', withPrisma);

app.get('/books', (c) => c.text('Hono!'));

export default app;
