import { useCallback, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { authClient } from '@/lib/auth-client';
import { getCurrentUser } from '@/api/me';
import { authContext } from './authContext';
import type { AuthenticatedUser } from './authContext';

interface Props {
	children: ReactNode;
}

export function AuthProvider({ children }: Props) {
	const [user, setUser] = useState<AuthenticatedUser | null>(null);
	const [isPending, setIsPending] = useState(true);

	const refreshAuth = useCallback(async () => {
		setIsPending(true);

		try {
			const currentUser = await getCurrentUser();
			setUser(currentUser);
			return currentUser;
		} catch {
			setUser(null);
			return null;
		} finally {
			setIsPending(false);
		}
	}, []);

	useEffect(() => {
		let isActive = true;

		void getCurrentUser()
			.then((currentUser) => {
				if (isActive) {
					setUser(currentUser);
				}
			})
			.catch(() => {
				if (isActive) {
					setUser(null);
				}
			})
			.finally(() => {
				if (isActive) {
					setIsPending(false);
				}
			});

		return () => {
			isActive = false;
		};
	}, []);

	const signOut = async () => {
		try {
			await authClient.signOut();
		} finally {
			setUser(null);
		}
	};

	const role = user?.role ?? null;

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
			{children}
		</authContext.Provider>
	);
}
