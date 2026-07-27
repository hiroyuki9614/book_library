/* eslint-disable @typescript-eslint/no-explicit-any */
import { createAuthClient } from 'better-auth/react';

const BASE = import.meta.env.VITE_API_BASE_URL ?? '';

const client = createAuthClient({ baseURL: BASE });

// Provide a small compatibility wrapper exposing the older convenience methods
// used across the codebase: signInEmail, getSession, signOut. Prefer the
// official client methods when available, otherwise fall back to direct fetch
// to the backend endpoints.
async function fallbackGetSession() {
	try {
		const res = await fetch(`${BASE}/api/auth/session`, { credentials: 'include' });
		if (!res.ok) return null;
		return await res.json();
	} catch {
		return null;
	}
}

async function fallbackSignInEmail({ email, password }: { email: string; password: string }) {
	const res = await fetch(`${BASE}/api/auth/sign-in/email`, {
		method: 'POST',
		credentials: 'include',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ email, password }),
	});
	if (res.ok) return { ok: true };
	try {
		const body = await res.json();
		return { ok: false, error: body?.error };
	} catch {
		return { ok: false, error: '認証に失敗しました' };
	}
}

async function fallbackSignOut() {
	await fetch(`${BASE}/api/auth/sign-out`, { method: 'POST', credentials: 'include' });
}

export const authClient: any = {
	// spread client so advanced APIs remain available
	...(client as any),
	// compatibility shims
	signInEmail: (payload: { email: string; password: string }) => {
		// prefer official client path if present
		try {
			const fn = (client as any).signIn?.email ?? (client as any).signInEmail;
			if (typeof fn === 'function') return fn(payload);
		} catch {
			/* fallthrough */
		}
		return fallbackSignInEmail(payload);
	},
	getSession: async () => {
		try {
			const fn = (client as any).getSession ?? (client as any).session?.get;
			if (typeof fn === 'function') return await fn();
		} catch {
			/* fallthrough */
		}
		return fallbackGetSession();
	},
	signOut: async () => {
		try {
			const fn = (client as any).signOut ?? (client as any).signOut?.post;
			if (typeof fn === 'function') return await fn();
		} catch {
			/* fallthrough */
		}
		return fallbackSignOut();
	},
};

export default authClient;
