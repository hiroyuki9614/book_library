import { describe, expect, test } from 'vitest';
import { app } from './index.js';

describe('OpenAPI', () => {
	test('OpenAPIドキュメントを返す', async () => {
		const res = await app.request('/doc');
		expect(res.status).toBe(200);
		expect(res.headers.get('content-type')).toContain('application/json');

		const document = await res.json();
		expect(document).toMatchObject({
			openapi: '3.1.0',
			info: {
				title: '書籍閲覧・管理API',
				version: '0.5.0',
			},
		});
		expect(document.paths).toHaveProperty('/health.get');
		expect(document.paths).toHaveProperty('/test.post');
		expect(document.paths).not.toHaveProperty('/doc');
		expect(document.paths).not.toHaveProperty('/scalar');
	});

	test('Scalar APIリファレンスを返す', async () => {
		const res = await app.request('/scalar');
		expect(res.status).toBe(200);

		const html = await res.text();
		expect(html).toContain('/doc');
	});

	test('フロントエンドからの認証APIプリフライトを許可する', async () => {
		const res = await app.request('/api/auth/get-session', {
			method: 'OPTIONS',
			headers: {
				Origin: 'http://localhost:5173',
				'Access-Control-Request-Method': 'GET',
			},
		});

		expect(res.status).toBe(204);
		expect(res.headers.get('access-control-allow-origin')).toBe('http://localhost:5173');
		expect(res.headers.get('access-control-allow-credentials')).toBe('true');
	});
});
