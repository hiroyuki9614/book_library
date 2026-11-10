import { describe, expect, it, vi } from 'vitest';
import { createHomeProgressResponseCapture, type HomeProgressRequest, type HomeProgressResponse } from './homeProgressResponses';

function request(url: string, navigation = false): HomeProgressRequest {
	return {
		url: () => url,
		method: () => 'GET',
		isNavigationRequest: () => navigation,
		frame: () => 'main-frame',
	};
}

function response(requestValue: HomeProgressRequest, body: unknown): HomeProgressResponse {
	return {
		url: () => requestValue.url(),
		status: () => 200,
		request: () => requestValue,
		json: vi.fn().mockResolvedValue(body),
	};
}

const pageBody = (page: number) => ({ total: 2, page, books: [] });

describe('home progress response capture', () => {
	it('ignores an old-document request that responds after the new navigation', () => {
		const capture = createHomeProgressResponseCapture((value) => value.isNavigationRequest() && value.frame() === 'main-frame');
		const oldRequest = request('http://localhost:3000/api/v1/books?limit=100&page=1');
		capture.onRequest(oldRequest);
		capture.onRequest(request('http://localhost:5173/', true));
		capture.onMainFrameNavigated();
		capture.onResponse(response(oldRequest, pageBody(1)));

		expect(capture.pages).toEqual([]);
	});

	it('starts reading a valid response body immediately and associates pages with their requests', async () => {
		const capture = createHomeProgressResponseCapture((value) => value.isNavigationRequest() && value.frame() === 'main-frame');
		capture.onRequest(request('http://localhost:5173/', true));
		capture.onMainFrameNavigated();
		const pageTwoRequest = request('http://localhost:3000/api/v1/books?limit=100&page=2');
		const pageOneRequest = request('http://localhost:3000/api/v1/books?limit=100&page=1');
		const pageTwoResponse = response(pageTwoRequest, pageBody(2));
		const pageOneResponse = response(pageOneRequest, pageBody(1));
		capture.onRequest(pageTwoRequest);
		capture.onRequest(pageOneRequest);
		capture.onResponse(pageTwoResponse);
		expect(pageTwoResponse.json).toHaveBeenCalledOnce();
		capture.onResponse(pageOneResponse);
		expect(pageOneResponse.json).toHaveBeenCalledOnce();
		await vi.waitFor(() => expect(capture.pages).toHaveLength(2));
		expect(capture.pages.map(({ requestedPage, body }) => [requestedPage, body.page]).sort()).toEqual([[1, 1], [2, 2]]);
	});

	it('surfaces asynchronous body read failures', async () => {
		const capture = createHomeProgressResponseCapture((value) => value.isNavigationRequest());
		capture.onRequest(request('http://localhost:5173/', true));
		capture.onMainFrameNavigated();
		const bookRequest = request('http://localhost:3000/api/v1/books?limit=100&page=1');
		const readError = new Error('response body read failed');
		const failedResponse = response(bookRequest, null);
		vi.mocked(failedResponse.json).mockRejectedValue(readError);
		capture.onRequest(bookRequest);
		capture.onResponse(failedResponse);

		await vi.waitFor(() => expect(() => capture.pages).toThrow(readError));
	});

	it('ignores later events after disposal', () => {
		const capture = createHomeProgressResponseCapture((value) => value.isNavigationRequest());
		capture.dispose();
		const navRequest = request('http://localhost:5173/', true);
		const bookRequest = request('http://localhost:3000/api/v1/books?limit=100&page=1');
		capture.onRequest(navRequest);
		capture.onMainFrameNavigated();
		capture.onRequest(bookRequest);
		const bookResponse = response(bookRequest, pageBody(1));
		capture.onResponse(bookResponse);

		expect(bookResponse.json).not.toHaveBeenCalled();
		expect(capture.pages).toEqual([]);
	});

	it('does not mix an already-reading response into a later navigation', async () => {
		const capture = createHomeProgressResponseCapture((value) => value.isNavigationRequest());
		capture.onRequest(request('http://localhost:5173/', true));
		capture.onMainFrameNavigated();
		const oldRequest = request('http://localhost:3000/api/v1/books?limit=100&page=1');
		let finishOld!: (body: unknown) => void;
		const oldResponse = response(oldRequest, null);
		vi.mocked(oldResponse.json).mockReturnValue(new Promise((resolve) => { finishOld = resolve; }));
		capture.onRequest(oldRequest);
		capture.onResponse(oldResponse);
		capture.onRequest(request('http://localhost:5173/', true));
		capture.onMainFrameNavigated();
		finishOld(pageBody(1));
		await Promise.resolve();
		expect(capture.pages).toEqual([]);
	});

	it('ignores a superseded-document body rejection without hiding current errors', async () => {
		const capture = createHomeProgressResponseCapture((value) => value.isNavigationRequest());
		capture.onRequest(request('http://localhost:5173/', true));
		capture.onMainFrameNavigated();
		const oldRequest = request('http://localhost:3000/api/v1/books?limit=100&page=1');
		let failOld!: (error: Error) => void;
		const oldResponse = response(oldRequest, null);
		vi.mocked(oldResponse.json).mockReturnValue(new Promise((_, reject) => { failOld = reject; }));
		capture.onRequest(oldRequest);
		capture.onResponse(oldResponse);
		capture.onRequest(request('http://localhost:5173/', true));
		capture.onMainFrameNavigated();
		failOld(new Error('old document disappeared'));
		await Promise.resolve();
		await Promise.resolve();
		expect(capture.pages).toEqual([]);
	});

	it('keeps the latest request for each page regardless of response completion order', async () => {
		const capture = createHomeProgressResponseCapture((value) => value.isNavigationRequest());
		capture.onRequest(request('http://localhost:5173/', true));
		capture.onMainFrameNavigated();
		const oldRequest = request('http://localhost:3000/api/v1/books?limit=100&page=1');
		let finishOld!: (body: unknown) => void;
		const oldResponse = response(oldRequest, null);
		vi.mocked(oldResponse.json).mockReturnValue(new Promise((resolve) => { finishOld = resolve; }));
		capture.onRequest(oldRequest);
		capture.onResponse(oldResponse);
		const newRequest = request('http://localhost:3000/api/v1/books?limit=100&page=1');
		capture.onRequest(newRequest);
		capture.onResponse(response(newRequest, { ...pageBody(1), total: 101 }));
		await vi.waitFor(() => expect(capture.pages).toHaveLength(1));
		finishOld(pageBody(1));
		await Promise.resolve();
		expect(capture.pages).toHaveLength(1);
		expect(capture.pages[0].body.total).toBe(101);
	});

	it('does not mutate captured state when an in-flight body finishes after disposal', async () => {
		const capture = createHomeProgressResponseCapture((value) => value.isNavigationRequest());
		capture.onRequest(request('http://localhost:5173/', true));
		capture.onMainFrameNavigated();
		const bookRequest = request('http://localhost:3000/api/v1/books?limit=100&page=1');
		let finish!: (body: unknown) => void;
		const bookResponse = response(bookRequest, null);
		vi.mocked(bookResponse.json).mockReturnValue(new Promise((resolve) => { finish = resolve; }));
		capture.onRequest(bookRequest);
		capture.onResponse(bookResponse);
		capture.dispose();
		finish(pageBody(1));
		await Promise.resolve();
		expect(capture.pages).toEqual([]);
	});
});
