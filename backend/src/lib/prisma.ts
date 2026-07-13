import 'dotenv/config';
import { Pool } from 'pg';
import { PrismaPg } from '@prisma/adapter-pg';
import { PrismaClient } from '../../prisma/generated/prisma/client.js';
import { createMiddleware } from 'hono/factory';

type PrismaVariables = {
	Variables: {
		prisma: PrismaClient;
	};
};

const connectionString = process.env.DATABASE_URL;

if (!connectionString) {
	throw new Error('DATABASE_URL is not defined');
}

const pool = new Pool({ connectionString });
const adapter = new PrismaPg(pool);
const prisma = new PrismaClient({ adapter });

const withPrisma = createMiddleware<PrismaVariables>(async (c, next) => {
	c.set('prisma', prisma);
	await next();
});

export type { PrismaVariables };
export default withPrisma;
