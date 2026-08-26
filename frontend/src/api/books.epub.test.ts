import { afterEach, describe, expect, test, vi } from 'vitest';
import { fetchBookFile } from './books';

describe('books API EPUB client', () => {
	afterEach(() => {
		vi.unstubAllEnvs();
		vi.restoreAllMocks();
	});

	test('EPUB file APIをcookie付きで呼び出しBlobを返す', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000/');
		const epub = new Blob(['PK EPUB DATA'], { type: 'application/epub+zip' });
		vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
			new Response(epub, { status: 200, headers: { 'Content-Type': 'application/epub+zip' } }),
		);

		const result = await fetchBookFile(7);

		expect(result).toBeInstanceOf(Blob);
		expect(result.type).toBe('application/epub+zip');
		expect(fetch).toHaveBeenCalledWith('http://localhost:3000/api/v1/books/7/file', { credentials: 'include' });
	});
});
