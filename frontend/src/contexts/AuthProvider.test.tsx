import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { cdp } from 'vitest/browser';
import { cleanup, render } from 'vitest-browser-react';
import { StrictMode } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { getCurrentUser } from '@/api/me';
import { authClient } from '@/lib/auth-client';
import { useReadingSave } from '@/hooks/useReadingSave';
import Login from '@/pages/Login';
import { AuthProvider } from './AuthProvider';
import { useAuth } from './useAuth';

const signOutSuccessResponse = { data: { success: true }, error: null } as const;

vi.mock('@/api/me', () => ({
	getCurrentUser: vi.fn(),
}));

vi.mock('@/lib/auth-client', () => ({
	authClient: {
		signOut: vi.fn(),
		signIn: {
			email: vi.fn(),
		},
	},
}));

function AuthState() {
	const { isPending, role, user } = useAuth();

	return (
		<div>
			<span>{isPending ? 'loading' : 'ready'}</span>
			<span>{user?.email ?? 'no-user'}</span>
			<span>{role ?? 'no-role'}</span>
		</div>
	);
}

function ReaderSaveControls() {
	const { user } = useAuth();
	const { save } = useReadingSave(99, user?.id);

	return (
		<div>
			<button type='button' onClick={() => save(2, 'reading', 20)}>queue progress</button>
			<button type='button' onClick={() => save(3, 'completed', 30)}>save immediately</button>
			<button type='button' onClick={() => save(4, 'reading', 40)}>queue later progress</button>
		</div>
	);
}

function AuthRaceHarness() {
	const { refreshAuth, user, logout } = useAuth();

	return (
		<div>
			<span data-testid='auth-user'>{user?.email ?? 'no-user'}</span>
			<button type='button' onClick={() => void refreshAuth()}>refresh auth</button>
			<button type='button' onClick={() => void logout().catch(() => undefined)}>logout</button>
			{user && <ReaderSaveControls />}
		</div>
	);
}

function LoginBoundaryHarness() {
	const { logout, user } = useAuth();

	return (
		<div>
			<span data-testid='login-boundary-user'>{user?.email ?? 'no-user'}</span>
			<button type='button' onClick={logout}>logout</button>
			<Login />
		</div>
	);
}

function deferred<T>() {
	let resolvePromise!: (value: T) => void;
	let rejectPromise!: (reason?: unknown) => void;
	const promise = new Promise<T>((resolve, reject) => {
		resolvePromise = resolve;
		rejectPromise = reject;
	});
	return {
		promise,
		resolve: (value?: T) => resolvePromise(value as T),
		reject: (reason?: unknown) => rejectPromise(reason),
	};
}

type BrowserPausedRequest = {
	requestId: string;
	request: { url: string; headers: Record<string, string> };
};

type BrowserCdpSession = {
	send: (method: string, params?: Record<string, unknown>) => Promise<unknown>;
	on: (event: string, listener: (payload: BrowserPausedRequest) => void) => void;
	off: (event: string, listener: (payload: BrowserPausedRequest) => void) => void;
};

describe('AuthProvider session loading', () => {
	beforeEach(() => {
		vi.resetAllMocks();
	});

	afterEach(async () => {
		await cleanup();
		vi.useRealTimers();
		vi.restoreAllMocks();
		vi.unstubAllEnvs();
	});

	test('認証済みユーザーAPI成功時にuserとroleを設定する', async () => {
		vi.mocked(getCurrentUser).mockResolvedValue({
			id: 1,
			name: 'Admin',
			email: 'admin@example.com',
			role: 'admin',
		});

		const { getByText } = await render(
			<MemoryRouter>
				<AuthProvider>
					<AuthState />
				</AuthProvider>
			</MemoryRouter>,
		);

		await expect.element(getByText('admin@example.com')).toBeInTheDocument();
		await expect.element(getByText('admin', { exact: true })).toBeInTheDocument();
		await expect.element(getByText('ready')).toBeInTheDocument();
	});

	test('認証済みユーザーAPI失敗時にもloadingを解除して未認証状態にする', async () => {
		vi.mocked(getCurrentUser).mockRejectedValue(new Error('network failure'));

		const { getByText } = await render(
			<MemoryRouter>
				<AuthProvider>
					<AuthState />
				</AuthProvider>
			</MemoryRouter>,
		);

		await expect.element(getByText('ready')).toBeInTheDocument();
		await expect.element(getByText('no-user')).toBeInTheDocument();
		await expect.element(getByText('no-role')).toBeInTheDocument();
	});

	test('ログアウト中の遅い/meを無効化し、保留中の認証再確認は新しいユーザーを返す', async () => {
		const oldUser = { id: 7, name: 'Old user', email: 'old@example.com', role: 'user' as const };
		const newUser = { id: 8, name: 'New user', email: 'new@example.com', role: 'user' as const };
		const delayedRefresh = deferred<typeof oldUser>();
		const pendingSignOut = deferred<typeof signOutSuccessResponse>();
		vi.mocked(authClient.signOut).mockReturnValue(pendingSignOut.promise);
		vi.mocked(getCurrentUser)
			.mockImplementationOnce(async () => oldUser)
			.mockImplementationOnce(() => delayedRefresh.promise)
			.mockRejectedValueOnce(new Error('API request failed: 401'))
			.mockResolvedValue(newUser);
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		const requests: Array<{ method: string; body: string; userId: string | null }> = [];
		vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
			const headers = new Headers(init?.headers);
			requests.push({ method: init?.method ?? 'GET', body: String(init?.body ?? ''), userId: headers.get('X-BeLib-User-Id') });
			return new Response(JSON.stringify({ bookId: 99, currentPosition: '2', currentPage: 2, progressPercentage: 20, readStatus: 'reading' }), {
				status: 200,
				headers: { 'Content-Type': 'application/json' },
			});
		});

		const { getByRole, getByTestId } = await render(
			<AuthProvider>
				<AuthRaceHarness />
			</AuthProvider>,
		);

		await expect.element(getByTestId('auth-user')).toHaveTextContent(oldUser.email);
		await getByRole('button', { name: 'refresh auth' }).click();
		await getByRole('button', { name: 'queue progress' }).click();
		await getByRole('button', { name: 'logout' }).click();
		await vi.waitFor(() => expect(authClient.signOut).toHaveBeenCalled(), { timeout: 2_000 });
		await expect.element(getByTestId('auth-user')).toHaveTextContent('no-user');

		await getByRole('button', { name: 'refresh auth' }).click();
		expect(getCurrentUser).toHaveBeenCalledTimes(2);
		await expect.element(getByTestId('auth-user')).toHaveTextContent('no-user');
		delayedRefresh.resolve(oldUser);
		await new Promise((resolve) => setTimeout(resolve, 0));
		await expect.element(getByTestId('auth-user')).toHaveTextContent('no-user');

		pendingSignOut.resolve(signOutSuccessResponse);
		await pendingSignOut.promise;
		await expect.element(getByTestId('auth-user')).toHaveTextContent(newUser.email);
		expect(getCurrentUser).toHaveBeenCalledTimes(4);

		await new Promise((resolve) => setTimeout(resolve, 850));
		const patchRequests = () => requests.filter((request) => request.method === 'PATCH');
		const oldSessionSave = {
			method: 'PATCH',
			body: JSON.stringify({ currentPage: 2, readStatus: 'reading', progressPercentage: 20, saveSequence: '1' }),
			userId: String(oldUser.id),
		};
		expect(patchRequests()).toEqual([oldSessionSave]);
		expect(patchRequests()[0]).toEqual({
			method: 'PATCH',
			body: JSON.stringify({ currentPage: 2, readStatus: 'reading', progressPercentage: 20, saveSequence: '1' }),
			userId: String(oldUser.id),
		});

		await getByRole('button', { name: 'queue progress' }).click();
		await vi.waitFor(() => expect(patchRequests()).toHaveLength(2), { timeout: 2_000 });
		expect(patchRequests()[0]).toEqual(oldSessionSave);
		expect(patchRequests()[1]).toEqual({
			method: 'PATCH',
			body: JSON.stringify({ currentPage: 2, readStatus: 'reading', progressPercentage: 20, saveSequence: '1' }),
			userId: String(newUser.id),
		});
	});

	test('同じユーザーの認証再確認では保留中の正当な保存を保持する', async () => {
		const user = { id: 7, name: 'User', email: 'user@example.com', role: 'user' as const };
		vi.mocked(getCurrentUser).mockResolvedValue(user);
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		const requests: Array<{ method: string; body: string; userId: string | null }> = [];
		vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
			const headers = new Headers(init?.headers);
			requests.push({ method: init?.method ?? 'GET', body: String(init?.body ?? ''), userId: headers.get('X-BeLib-User-Id') });
			return new Response(JSON.stringify({ bookId: 99, currentPosition: '2', currentPage: 2, progressPercentage: 20, readStatus: 'reading' }), {
				status: 200,
				headers: { 'Content-Type': 'application/json' },
			});
		});

		const { getByRole, getByTestId } = await render(
			<StrictMode>
				<AuthProvider>
					<AuthRaceHarness />
				</AuthProvider>
			</StrictMode>,
		);

		await expect.element(getByTestId('auth-user')).toHaveTextContent(user.email);
		await getByRole('button', { name: 'queue progress' }).click();
		await getByRole('button', { name: 'refresh auth' }).click();
		await vi.waitFor(() => expect(requests.filter((request) => request.method === 'PATCH')).toHaveLength(1), { timeout: 2_000 });
		expect(requests.find((request) => request.method === 'PATCH')).toMatchObject({ userId: String(user.id) });
	});

	test('ログアウトのerror応答後は旧ユーザーを拒否し、再試行で回復できる', async () => {
		const oldUser = { id: 7, name: 'Old user', email: 'old@example.com', role: 'user' as const };
		const delayedRefresh = deferred<typeof oldUser>();
		const failedSignOut = deferred<{ data: null; error: { message: string } }>();
		vi.mocked(authClient.signOut).mockReturnValue(failedSignOut.promise);
		vi.mocked(getCurrentUser)
			.mockImplementationOnce(async () => oldUser)
			.mockImplementationOnce(() => delayedRefresh.promise);

		const { getByRole, getByTestId } = await render(
			<AuthProvider>
				<AuthRaceHarness />
			</AuthProvider>,
		);

		await expect.element(getByTestId('auth-user')).toHaveTextContent(oldUser.email);
		await getByRole('button', { name: 'refresh auth' }).click();
		await getByRole('button', { name: 'logout' }).click();
		await getByRole('button', { name: 'refresh auth' }).click();
		expect(getCurrentUser).toHaveBeenCalledTimes(2);
		await expect.element(getByTestId('auth-user')).toHaveTextContent('no-user');

		failedSignOut.resolve({ data: null, error: { message: 'sign-out failed' } });
		await failedSignOut.promise;
		await expect.element(getByTestId('auth-signout-uncertain')).toBeInTheDocument();
		expect(getCurrentUser).toHaveBeenCalledTimes(2);
		delayedRefresh.resolve(oldUser);
		await new Promise((resolve) => setTimeout(resolve, 0));
		await expect.element(getByTestId('auth-user')).toHaveTextContent('no-user');

		vi.mocked(getCurrentUser).mockRejectedValue(new Error('API request failed: 401'));
		vi.mocked(authClient.signOut).mockResolvedValue(signOutSuccessResponse);
		await getByRole('button', { name: 'ログアウトを再試行' }).click();
		await expect.element(getByTestId('auth-signout-uncertain')).not.toBeInTheDocument();
		await expect.element(getByTestId('auth-user')).toHaveTextContent('no-user');
	});

	test('成功形のログアウト応答だけでは旧セッションを解放しない', async () => {
		const oldUser = { id: 7, name: 'Old user', email: 'old@example.com', role: 'user' as const };
		vi.mocked(getCurrentUser)
			.mockResolvedValueOnce(oldUser)
			.mockResolvedValueOnce(oldUser);
		vi.mocked(authClient.signOut).mockResolvedValue(signOutSuccessResponse);

		const { getByRole, getByTestId } = await render(
			<AuthProvider>
				<AuthRaceHarness />
			</AuthProvider>,
		);

		await expect.element(getByTestId('auth-user')).toHaveTextContent(oldUser.email);
		await getByRole('button', { name: 'logout' }).click();
		await expect.element(getByTestId('auth-user')).toHaveTextContent('no-user');
		await expect.element(getByTestId('auth-signout-uncertain')).toBeInTheDocument();
		expect(getCurrentUser).toHaveBeenCalledTimes(2);
	});

	test('ログアウトの拒否を未処理にせず、SideNavと同じonClickを安全に完了する', async () => {
		const user = { id: 7, name: 'User', email: 'user@example.com', role: 'user' as const };
		vi.mocked(getCurrentUser).mockResolvedValue(user);
		vi.mocked(authClient.signOut).mockRejectedValue(new Error('sign-out failed'));
		const unhandledRejections: unknown[] = [];
		const onUnhandledRejection = (event: PromiseRejectionEvent) => {
			unhandledRejections.push(event.reason);
			event.preventDefault();
		};
		window.addEventListener('unhandledrejection', onUnhandledRejection);

		try {
			const { getByRole, getByTestId } = await render(
				<MemoryRouter>
					<AuthProvider>
						<LoginBoundaryHarness />
					</AuthProvider>
				</MemoryRouter>,
			);

			await expect.element(getByTestId('login-boundary-user')).toHaveTextContent(user.email);
			await getByRole('button', { name: 'logout' }).click();
			await expect.element(getByTestId('login-boundary-user')).toHaveTextContent('no-user');
			await expect.element(getByTestId('auth-signout-uncertain')).toBeInTheDocument();
			await new Promise((resolve) => setTimeout(resolve, 0));
			expect(unhandledRejections).toHaveLength(0);
		} finally {
			window.removeEventListener('unhandledrejection', onUnhandledRejection);
		}
	});

	test('タイムアウト後はログイン境界を解放せず、明示的な再試行で回復する', async () => {
		vi.useFakeTimers();
		const oldUser = { id: 7, name: 'Old user', email: 'old@example.com', role: 'user' as const };
		const newUser = { id: 8, name: 'New user', email: 'new@example.com', role: 'user' as const };
		let signOutCall = 0;
		const pendingOldSignOut = deferred<typeof signOutSuccessResponse>();
		const pendingNewSignOut = deferred<typeof signOutSuccessResponse>();

		vi.mocked(getCurrentUser)
			.mockResolvedValueOnce(oldUser)
			.mockResolvedValueOnce(oldUser)
			.mockRejectedValueOnce(new Error('API request failed: 401'))
			.mockResolvedValue(newUser);
		vi.mocked(authClient.signOut).mockImplementation(() => {
			signOutCall += 1;
			if (signOutCall === 1) {
				return pendingOldSignOut.promise;
			}
			return pendingNewSignOut.promise;
		});
		vi.mocked(authClient.signIn.email).mockImplementation(async () => {
			return {
				data: {
					token: 'new-session-token',
					user: {
						id: String(newUser.id),
						name: newUser.name,
						email: newUser.email,
						emailVerified: true,
						image: null,
						createdAt: new Date(),
						updatedAt: new Date(),
					},
				},
				error: null,
			};
		});

		const { getByLabelText, getByRole, getByTestId } = await render(
			<MemoryRouter initialEntries={['/login']}>
				<AuthProvider>
					<LoginBoundaryHarness />
				</AuthProvider>
			</MemoryRouter>,
		);

		await expect.element(getByTestId('login-boundary-user')).toHaveTextContent(oldUser.email);
		await getByLabelText('ユーザーID').fill(newUser.email);
		await getByLabelText('パスワード').fill('new-password');
		await getByRole('button', { name: 'logout' }).click();
		await Promise.resolve();
		await expect.element(getByRole('button', { name: 'Submit' })).toBeDisabled();
		expect(authClient.signIn.email).not.toHaveBeenCalled();

		await vi.advanceTimersByTimeAsync(5_000);
		await Promise.resolve();
		expect(authClient.signOut).toHaveBeenCalledTimes(1);
		await expect.element(getByRole('button', { name: 'Submit' })).toBeDisabled();
		await expect.element(getByTestId('auth-signout-uncertain')).toBeInTheDocument();
		expect(authClient.signIn.email).not.toHaveBeenCalled();
		await getByRole('button', { name: 'ログアウトを再試行' }).click();
		await Promise.resolve();
		expect(authClient.signOut).toHaveBeenCalledTimes(1);

		pendingOldSignOut.resolve(signOutSuccessResponse);
		await pendingOldSignOut.promise;
		await Promise.resolve();
		await expect.element(getByTestId('auth-signout-uncertain')).toBeInTheDocument();

		await getByRole('button', { name: 'ログアウトを再試行' }).click();
		await Promise.resolve();
		expect(authClient.signOut).toHaveBeenCalledTimes(2);
		pendingNewSignOut.resolve(signOutSuccessResponse);
		await pendingNewSignOut.promise;
		await expect.element(getByTestId('auth-signout-uncertain')).not.toBeInTheDocument();
		await expect.element(getByRole('button', { name: 'Submit' })).not.toBeDisabled();

		await getByRole('button', { name: 'Submit' }).click();
		await expect.element(getByTestId('login-boundary-user')).toHaveTextContent(newUser.email);
		expect(authClient.signIn.email).toHaveBeenCalledWith({ email: newUser.email, password: 'new-password' });
	});

	test('認証無効化はabortし、旧リーダーの保留保存から新しいPATCHを送らない', async () => {
		const user = { id: 7, name: 'User', email: 'user@example.com', role: 'user' as const };
		vi.mocked(getCurrentUser).mockResolvedValue(user);
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		const requests: Array<{ method: string; userId: string | null; signal?: AbortSignal }> = [];
		vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
			const headers = new Headers(init?.headers);
			const request = { method: init?.method ?? 'GET', userId: headers.get('X-BeLib-User-Id'), signal: init?.signal ?? undefined };
			requests.push(request);
			if (request.method === 'PATCH') {
				return new Promise<Response>((_resolve, reject) => {
					const abort = () => reject(new DOMException('Request aborted', 'AbortError'));
					if (request.signal?.aborted) abort();
					else request.signal?.addEventListener('abort', abort, { once: true });
				});
			}
			return new Response(null, { status: 204 });
		});

		const { getByRole, getByTestId } = await render(
			<AuthProvider>
				<AuthRaceHarness />
			</AuthProvider>,
		);

		await expect.element(getByTestId('auth-user')).toHaveTextContent(user.email);
		await getByRole('button', { name: 'save immediately' }).click();
		await vi.waitFor(() => expect(requests.filter((request) => request.method === 'PATCH')).toHaveLength(1));
		await getByRole('button', { name: 'queue later progress' }).click();
		await getByRole('button', { name: 'logout' }).click();
		await expect.element(getByTestId('auth-user')).toHaveTextContent('no-user');

		const patchRequests = requests.filter((request) => request.method === 'PATCH');
		expect(patchRequests).toHaveLength(1);
		expect(patchRequests[0].signal).toBeDefined();
		expect(patchRequests[0].signal?.aborted).toBe(true);
		await new Promise((resolve) => setTimeout(resolve, 850));
		expect(requests.filter((request) => request.method === 'PATCH')).toHaveLength(1);
	});

	test('Chromiumのlocalhost fetchでabort・成功・遅延Set-Cookie競合と回復を実測する', async () => {
		// This is browser transport coverage only; it does not call Better Auth or /me.
		// The browser bundle cannot bind a Node listener; CDP fulfills this local-origin
		// request inside Chromium so its real fetch/cookie jar processes Set-Cookie.
		const cdpSession = cdp() as unknown as BrowserCdpSession;
		const origin = `http://localhost:${location.port}`;
		const cookieName = `belib-auth-test-${crypto.randomUUID()}`;
		const endpoint = `${origin}/__belib-auth-transport-test/${cookieName}`;
		const cookieForNewSession = `${cookieName}=new-session; Path=/; HttpOnly; SameSite=Lax`;
		const cookieForClearedSession = `${cookieName}=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax`;
		const timeoutPaused = deferred<BrowserPausedRequest>();
		const delayedOldSignOutPaused = deferred<BrowserPausedRequest>();
		let transportError: unknown;

		const fulfill = async (requestId: string, setCookie: string) => {
			await cdpSession.send('Fetch.fulfillRequest', {
				requestId,
				responseCode: 200,
				responseHeaders: [
					{ name: 'Content-Type', value: 'application/json' },
					{ name: 'Set-Cookie', value: setCookie },
				],
				body: btoa(JSON.stringify({ ok: true })),
			});
		};
		const onRequestPaused = (event: BrowserPausedRequest) => {
			const requestUrl = new URL(event.request.url);
			const operation = requestUrl.searchParams.get('operation');
			if (!operation) {
				void cdpSession.send('Fetch.continueRequest', { requestId: event.requestId }).catch((error: unknown) => { transportError = error; });
				return;
			}
			if (operation === 'timeout') {
				timeoutPaused.resolve(event);
				return;
			}
			if (operation === 'old-sign-out') {
				delayedOldSignOutPaused.resolve(event);
				return;
			}
			const setCookie = operation === 'new-sign-in' || operation === 'recovery-sign-in'
				? cookieForNewSession
				: cookieForClearedSession;
			void fulfill(event.requestId, setCookie).catch((error: unknown) => { transportError = error; });
		};

		await cdpSession.send('Fetch.disable').catch(() => undefined);
		cdpSession.on('Fetch.requestPaused', onRequestPaused);
		await cdpSession.send('Fetch.enable', {
			patterns: [{ urlPattern: `${origin}/__belib-auth-transport-test/*`, requestStage: 'Request' }],
		});

		const readCookie = async () => {
			const result = await cdpSession.send('Network.getCookies', { urls: [`${origin}/`] }) as { cookies: Array<{ name: string; value: string }> };
			return result.cookies.find((cookie) => cookie.name === cookieName)?.value ?? null;
		};
		const clearCookie = async () => {
			await cdpSession.send('Network.deleteCookies', { name: cookieName, url: `${origin}/` });
		};

		try {
			await cdpSession.send('Network.setCookie', {
				name: cookieName,
				value: 'old-session',
				url: `${origin}/`,
				httpOnly: true,
				sameSite: 'Lax',
			});
			expect(await readCookie()).toBe('old-session');

			const timeoutController = new AbortController();
			const timeoutRequest = fetch(`${endpoint}?operation=timeout`, { method: 'POST', credentials: 'include', signal: timeoutController.signal });
			const timeoutRequestPaused = await timeoutPaused.promise;
			expect(timeoutRequestPaused.request.headers.Cookie ?? timeoutRequestPaused.request.headers.cookie).toContain(`${cookieName}=old-session`);
			timeoutController.abort();
			await expect(timeoutRequest).rejects.toMatchObject({ name: 'AbortError' });
			expect(timeoutController.signal.aborted).toBe(true);
			expect(await readCookie()).toBe('old-session');

			const successfulSignOut = fetch(`${endpoint}?operation=successful-sign-out`, { method: 'POST', credentials: 'include' });
			await expect(successfulSignOut).resolves.toMatchObject({ status: 200 });
			await expect.poll(readCookie).toBe(null);

			await cdpSession.send('Network.setCookie', {
				name: cookieName,
				value: 'old-session',
				url: `${origin}/`,
				httpOnly: true,
				sameSite: 'Lax',
			});
			const delayedOldSignOut = fetch(`${endpoint}?operation=old-sign-out`, { method: 'POST', credentials: 'include' });
			const delayedResponse = await delayedOldSignOutPaused.promise;
			expect(delayedResponse.request.headers.Cookie ?? delayedResponse.request.headers.cookie).toContain(`${cookieName}=old-session`);

			const newSignIn = await fetch(`${endpoint}?operation=new-sign-in`, { method: 'POST', credentials: 'include' });
			expect(newSignIn.status).toBe(200);
			await expect.poll(readCookie).toBe('new-session');

			await fulfill(delayedResponse.requestId, cookieForClearedSession);
			expect((await delayedOldSignOut).status).toBe(200);
			await expect.poll(readCookie).toBe(null);

			const recoverySignOut = await fetch(`${endpoint}?operation=recovery-sign-out`, { method: 'POST', credentials: 'include' });
			expect(recoverySignOut.status).toBe(200);
			await expect.poll(readCookie).toBe(null);
			const recoverySignIn = await fetch(`${endpoint}?operation=recovery-sign-in`, { method: 'POST', credentials: 'include' });
			expect(recoverySignIn.status).toBe(200);
			await expect.poll(readCookie).toBe('new-session');
			expect(transportError).toBeUndefined();
		} finally {
			await clearCookie();
			cdpSession.off('Fetch.requestPaused', onRequestPaused);
			await cdpSession.send('Fetch.disable').catch(() => undefined);
		}
	});
});
