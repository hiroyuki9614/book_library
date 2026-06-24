import { serve } from '@hono/node-server';
import { Hono } from 'hono';

const app = new Hono();

app.get('/health', (c) => {
	return c.json({
		status: 'ok',
		service: 'belib-api',
	});
});

const port = Number(process.env.APP_PORT ?? 3000);

serve({
	fetch: app.fetch,
	port,
	hostname: '0.0.0.0',
});

console.log(`Server is running on http://0.0.0.0:${port}`);
