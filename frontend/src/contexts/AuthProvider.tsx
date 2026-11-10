import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { invalidateReadingSaves } from '@/api/readingInfo';
import { authClient } from '@/lib/auth-client';
import { getCurrentUser } from '@/api/me';
import { authContext } from './authContext';
import type { AuthenticatedUser } from './authContext';

interface Props {
	children: ReactNode;
}

const SIGN_OUT_REFRESH_WAIT_TIMEOUT_MS = 5_000;

type SignOutStatus = 'idle' | 'pending' | 'uncertain';

function retireAuthRequestGeneration(generation: { current: number }) {
	generation.current += 1;
}

type SignOutAttempt = {
	resolveCompletion: () => void;
	timeoutId: ReturnType<typeof setTimeout> | undefined;
	generation: number;
	retired: boolean;
};

function createSignOutCompletion() {
	let resolveCompletion!: () => void;
	const completion = new Promise<void>((resolve) => {
		resolveCompletion = resolve;
	});
	return { completion, resolveCompletion };
}

function isSuccessfulSignOutResponse(result: unknown) {
	if (!result || typeof result !== 'object') return false;
	const response = result as { data?: unknown; error?: unknown };
	if (response.error) return false;
	if (!response.data || typeof response.data !== 'object') return false;
	return (response.data as { success?: unknown }).success === true;
}

function isUnauthenticatedCurrentUserError(error: unknown) {
	return error instanceof Error && error.message === 'API request failed: 401';
}

async function verifySessionAbsence() {
	try {
		await getCurrentUser();
		return false;
	} catch (error) {
		// A network/5xx failure is not proof that Better Auth invalidated the
		// session. Only the protected /me 401 is safe to use as signout proof.
		return isUnauthenticatedCurrentUserError(error);
	}
}

export function AuthProvider({ children }: Props) {
	const [user, setUser] = useState<AuthenticatedUser | null>(null);
	const [isPending, setIsPending] = useState(true);
	const currentUserRef = useRef<AuthenticatedUser | null>(null);
	const authRequestGeneration = useRef(0);
	const isMounted = useRef(false);
	const isSigningOut = useRef(false);
	const inFlightSignOut = useRef<Promise<void> | null>(null);
	const signOutAttempt = useRef<SignOutAttempt | null>(null);
	const signOutStatusRef = useRef<SignOutStatus>('idle');
	const [signOutStatus, setSignOutStatus] = useState<SignOutStatus>('idle');

	const applyUser = useCallback((nextUser: AuthenticatedUser | null) => {
		if (currentUserRef.current?.id !== nextUser?.id) invalidateReadingSaves();
		currentUserRef.current = nextUser;
		setUser(nextUser);
	}, []);

	const refreshAuth = useCallback(async () => {
		setIsPending(true);
		const signOutRequest = inFlightSignOut.current;
		if (signOutRequest) {
			await signOutRequest;
			if (!isMounted.current) return null;
		}
		if (signOutStatusRef.current !== 'idle') return null;

		setIsPending(true);
		const requestGeneration = ++authRequestGeneration.current;

		try {
			const authenticatedUser = await getCurrentUser();
			if (!isMounted.current || requestGeneration !== authRequestGeneration.current) return null;
			applyUser(authenticatedUser);
			return authenticatedUser;
		} catch {
			if (!isMounted.current || requestGeneration !== authRequestGeneration.current) return null;
			applyUser(null);
			return null;
		} finally {
			if (isMounted.current && requestGeneration === authRequestGeneration.current) setIsPending(false);
		}
	}, [applyUser]);

	useEffect(() => {
		isMounted.current = true;
		const requestGeneration = ++authRequestGeneration.current;

		void getCurrentUser().then(
			(authenticatedUser) => {
				if (!isMounted.current || requestGeneration !== authRequestGeneration.current) return;
				applyUser(authenticatedUser);
				setIsPending(false);
			},
			() => {
				if (!isMounted.current || requestGeneration !== authRequestGeneration.current) return;
				applyUser(null);
				setIsPending(false);
			},
		);

		return () => {
			isMounted.current = false;
			retireAuthRequestGeneration(authRequestGeneration);
		};
	}, [applyUser]);

	const signOut = useCallback(async () => {
		if (isSigningOut.current) return inFlightSignOut.current ?? Promise.resolve();
		isSigningOut.current = true;
		signOutStatusRef.current = 'pending';
		setSignOutStatus('pending');
		const signOutGeneration = ++authRequestGeneration.current;
		// Retire reader cleanup flushes before changing the identity and before
		// Better Auth clears the cookie.
		invalidateReadingSaves();
		currentUserRef.current = null;
		setUser(null);
		// GuestRoute and Login both use isPending as the session boundary. Keep
		// them blocked until the server confirms logout; a client-side timeout is
		// not confirmation.
		setIsPending(true);

		const { completion, resolveCompletion } = createSignOutCompletion();
		const attempt: SignOutAttempt = {
			resolveCompletion,
			timeoutId: undefined,
			generation: signOutGeneration,
			retired: false,
		};
		signOutAttempt.current = attempt;
		inFlightSignOut.current = completion;

		const finishSignOut = (status: Exclude<SignOutStatus, 'pending'>) => {
			if (attempt.retired) return;
			attempt.retired = true;
			if (attempt.timeoutId !== undefined) clearTimeout(attempt.timeoutId);
			if (signOutAttempt.current === attempt) {
				signOutAttempt.current = null;
				inFlightSignOut.current = null;
				isSigningOut.current = false;
			}
			signOutStatusRef.current = status;
			if (isMounted.current) {
				setSignOutStatus(status);
				if (status === 'uncertain') setIsPending(true);
				else if (attempt.generation === authRequestGeneration.current) setIsPending(false);
			}
			resolveCompletion();
		};

		attempt.timeoutId = setTimeout(() => {
			if (attempt.retired) return;
			// This is only a UI deadline. Keep observing the request because an
			// AbortController cannot undo server-side work or a late Set-Cookie.
			signOutStatusRef.current = 'uncertain';
			if (isMounted.current) {
				setSignOutStatus('uncertain');
				setIsPending(true);
			}
		}, SIGN_OUT_REFRESH_WAIT_TIMEOUT_MS);
		const authSignOutRequest = Promise.resolve().then(() => authClient.signOut());
		void authSignOutRequest.then(
			async (result) => {
				if (!isSuccessfulSignOutResponse(result)) {
					finishSignOut('uncertain');
					return;
				}

				const sessionAbsent = await verifySessionAbsence();
				finishSignOut(sessionAbsent ? 'idle' : 'uncertain');
			},
			() => finishSignOut('uncertain'),
		);
		return completion;
	}, []);

	const role = user?.role ?? null;
	const isSignOutUncertain = signOutStatus === 'uncertain';

	return (
		<authContext.Provider
			value={{
				user,
				isPending,
				isAuthenticated: Boolean(user),
				role,
				refreshAuth,
				signOut,
				logout: signOut,
			}}
		>
			{isSignOutUncertain && (
				<div role='alert' aria-live='assertive' data-testid='auth-signout-uncertain'>
					<p>ログアウトの完了を確認できません。セッションを確認できるまでログインを停止しています。</p>
					<button type='button' autoFocus onClick={() => void signOut()}>ログアウトを再試行</button>
				</div>
			)}
			<div aria-hidden={isSignOutUncertain ? 'true' : undefined} inert={isSignOutUncertain || undefined} style={isSignOutUncertain ? { visibility: 'hidden' } : undefined}>
				{children}
			</div>
		</authContext.Provider>
	);
}
