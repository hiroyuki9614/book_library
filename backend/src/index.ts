import { serve } from '@hono/node-server';
import { Scalar } from '@scalar/hono-api-reference';
import { Hono } from 'hono';
import { describeRoute } from 'hono-openapi';
import { createOpenApiHandler } from './routes/openApi/route.js';

const app = new Hono();
const environment = process.env.ENVIRONMENT;

app.get(
	'/health',
	describeRoute({
		tags: ['System'],
		summary: 'ヘルスチェック',
		responses: {
			200: {
				description: 'APIは正常に稼働しています',
				content: {
					'application/json': {
						schema: {
							type: 'object',
							properties: {
								status: { type: 'string', example: 'ok' },
								service: { type: 'string', example: 'belib-api' },
							},
							required: ['status', 'service'],
						},
					},
				},
			},
		},
	}),
	(c) => {
		return c.json({
			status: 'ok',
			service: 'belib-api',
		});
	},
);

app.get('/doc', createOpenApiHandler(app));

app.get(
	'/scalar',
	Scalar(() => {
		return {
			url: '/doc',
			proxyUrl: environment === 'development' ? 'https://proxy.scalar.com' : undefined,
		};
	}),
);

app.post(
	'/test',
	describeRoute({
		tags: ['Development'],
		summary: 'JSONリクエストの疎通確認',
		requestBody: {
			required: true,
			content: {
				'application/json': {
					schema: { type: 'object', additionalProperties: true },
				},
			},
		},
		responses: {
			200: {
				description: '受信したJSONを返します',
				content: {
					'application/json': {
						schema: {
							type: 'object',
							properties: {
								message: { type: 'string' },
								data: { type: 'object', additionalProperties: true },
							},
							required: ['message', 'data'],
						},
					},
				},
			},
		},
	}),
	async (c) => {
		const body = await c.req.json();
		return c.json({
			message: 'Received data',
			data: body,
		});
	},
);

export { app };
export default app;

const port = Number(process.env.APP_PORT ?? 3000);

serve({
	fetch: app.fetch,
	port,
	hostname: '0.0.0.0',
});

console.log(`Server is running on http://0.0.0.0:${port}`);
