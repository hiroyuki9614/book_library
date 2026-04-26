import { serve } from '@hono/node-server';
import { Hono } from 'hono';
import { cors } from 'hono/cors';

const app = new Hono();

app.use(
	'/api/*',
	cors({
		origin: 'http://localhost:5173',
	})
);

app.get('/api/v1/books', (c) => {
	return c.json([
		{ id: 1, title: 'The Great Gatsby', author: 'F. Scott Fitzgerald' },
		{ id: 2, title: 'The Catcher in the Rye', author: 'J.D. Salinger' },
	]);
});

serve(
	{
		fetch: app.fetch,
		port: 5173,
	},
	(info) => {
		console.log(`Server is running on http://localhost:${info.port}`);
	}
);

export { app };
