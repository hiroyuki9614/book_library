import type { Hono } from 'hono';
import { openAPIRouteHandler } from 'hono-openapi';

export const createOpenApiHandler = (app: Hono) =>
	openAPIRouteHandler(app, {
		documentation: {
			info: {
				title: '書籍閲覧・管理API',
				version: '0.5.0',
				description:
					'EPUB・PDF形式の書籍を閲覧・管理するアプリケーションのバックエンドAPIです。',
			},
			servers: [
				{
					url: 'https://app.hiroyuki9614.com/api/v1',
					description: 'Production',
				},
				{
					url: 'http://localhost:3000',
					description: 'Local development',
				},
			],
		},
		exclude: ['/doc', '/scalar'],
	});
