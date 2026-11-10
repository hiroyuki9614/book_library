export type HomeProgressListPage = {
	total: number;
	page: number;
	books: Array<{ id: number; title: string; progressPercentage: number | null }>;
};

export type HomeProgressRequest = {
	url(): string;
	method(): string;
	isNavigationRequest(): boolean;
	frame(): unknown;
};

export type HomeProgressResponse = {
	url(): string;
	status(): number;
	request(): HomeProgressRequest;
	json(): Promise<unknown>;
};

export type CapturedHomeProgressPage = {
	requestedPage: number;
	url: string;
	status: number;
	body: HomeProgressListPage;
};

/** Capture book-list responses belonging to requests made after the selected document navigation. */
export function createHomeProgressResponseCapture(
	isTargetMainFrameNavigation: (request: HomeProgressRequest) => boolean,
) {
	let navigationRequested = false;
	let navigationGeneration = 0;
	let documentNavigated = false;
	let disposed = false;
	let failure: unknown;
	let hasFailure = false;
	const requestedPages = new Map<HomeProgressRequest, number>();
	const pages = new Map<number, CapturedHomeProgressPage>();
	const latestRequests = new Map<number, HomeProgressRequest>();

	function onRequest(request: HomeProgressRequest) {
		if (disposed) return;
		if (isTargetMainFrameNavigation(request)) {
			navigationRequested = true;
			navigationGeneration += 1;
			documentNavigated = false;
			requestedPages.clear();
			pages.clear();
			latestRequests.clear();
			failure = undefined;
			hasFailure = false;
		}
		if (!documentNavigated || request.method() !== 'GET') return;

		const url = new URL(request.url());
		if (url.pathname !== '/api/v1/books' || url.searchParams.get('limit') !== '100') return;
		const page = url.searchParams.get('page');
		if (page && /^\d+$/.test(page)) {
			const requestedPage = Number(page);
			requestedPages.set(request, requestedPage);
			latestRequests.set(requestedPage, request);
			pages.delete(requestedPage);
		}
	}

	function onMainFrameNavigated() {
		if (!disposed && navigationRequested) documentNavigated = true;
	}

	function onResponse(response: HomeProgressResponse) {
		if (disposed) return;
		const request = response.request();
		const requestedPage = requestedPages.get(request);
		if (requestedPage === undefined) return;
		requestedPages.delete(request);
		const generation = navigationGeneration;
		const isCurrent = () => !disposed && generation === navigationGeneration
			&& latestRequests.get(requestedPage) === request;
		if (!isCurrent()) return;

		// Start reading in the response event, while Playwright still has the response body available.
		void response.json().then((body) => {
			if (!isCurrent()) return;
			pages.set(requestedPage, {
				requestedPage,
				url: response.url(),
				status: response.status(),
				body: body as HomeProgressListPage,
			});
		}).catch((error: unknown) => {
			if (!isCurrent()) return;
			if (!hasFailure) failure = error;
			hasFailure = true;
		});
	}

	return {
		onRequest,
		onMainFrameNavigated,
		onResponse,
		get pages() {
			if (hasFailure) throw failure;
			return [...pages.values()];
		},
		dispose() {
			disposed = true;
			requestedPages.clear();
		},
	};
}
