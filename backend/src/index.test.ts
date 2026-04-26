import { expect, test } from 'vitest';
import { app } from './index.js';

test('should return JSON', async () => {
	const res = await app.request('http://localhost/api/v1/books');
	expect(res.status).toBe(200);

	const data = await res.json();
	expect(data).toEqual([
		{ id: 1, title: 'The Great Gatsby', author: 'F. Scott Fitzgerald' },
		{ id: 2, title: 'The Catcher in the Rye', author: 'J.D. Salinger' },
	]);
});
