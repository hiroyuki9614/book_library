import { Hono } from 'hono';
const app = new Hono();
app.get('/', async (c) => {
    try {
        const prisma = c.get('prisma');
        const books = await prisma.book.findMany();
        console.log('Fetched books:', books);
        return c.json(books);
    }
    catch (error) {
        console.error(error);
        return c.json({ error: 'Failed to fetch books' }, 500);
    }
});
export default app;
