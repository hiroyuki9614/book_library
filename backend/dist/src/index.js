import { serve } from '@hono/node-server';
import { Scalar } from '@scalar/hono-api-reference';
import { Hono } from 'hono';
import { cors } from 'hono/cors';
import { describeRoute } from 'hono-openapi';
import { pathToFileURL } from 'node:url';
import { auth } from './lib/auth.js';
import withPrisma from './lib/prisma.js';
import { createOpenApiHandler } from './routes/openApi/route.js';
import { getMe } from './routes/me/route.js';
const app = new Hono();
const environment = process.env.ENVIRONMENT;
const frontendUrl = process.env.FRONTEND_URL ?? 'http://localhost:5173';
app.use('/api/*', cors({
    origin: frontendUrl,
    allowHeaders: ['Content-Type'],
    allowMethods: ['GET', 'POST', 'OPTIONS'],
    credentials: true,
}));
app.get('/health', describeRoute({
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
}), (c) => {
    return c.json({
        status: 'ok',
        service: 'belib-api',
    });
});
app.on(['GET', 'POST'], '/api/auth/*', (c) => {
    return auth.handler(c.req.raw);
});
app.get('/api/v1/me', getMe);
app.get('/doc', createOpenApiHandler(app));
app.get('/scalar', Scalar(() => {
    return {
        url: '/doc',
        proxyUrl: environment === 'development' ? 'https://proxy.scalar.com' : undefined,
    };
}));
app.post('/test', describeRoute({
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
}), async (c) => {
    const body = await c.req.json();
    return c.json({
        message: 'Received data',
        data: body,
    });
});
export { app };
export default app;
const port = Number(process.env.APP_PORT ?? 3000);
const isEntryPoint = process.argv[1]
    ? import.meta.url === pathToFileURL(process.argv[1]).href
    : false;
if (isEntryPoint) {
    serve({
        fetch: app.fetch,
        port,
        hostname: '0.0.0.0',
    });
    console.log(`Server is running on http://0.0.0.0:${port}`);
}
