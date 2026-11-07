import { useEffect, useSyncExternalStore } from 'react';
import { useAuth } from '@/contexts/useAuth';
import type { AuthContextType } from '@/contexts/authContext';

type RefreshSnapshot = {
	isRefreshing: boolean;
	generation: number;
	hasValidated: boolean;
};

type RefreshCoordinator = {
	snapshot: RefreshSnapshot;
	listeners: Set<() => void>;
	isRunning: boolean;
	runAgain: boolean;
	refresh: () => Promise<void>;
};

const coordinators = new WeakMap<AuthContextType['refreshAuth'], RefreshCoordinator>();

function getCoordinator(refreshAuth: AuthContextType['refreshAuth']) {
	const existing = coordinators.get(refreshAuth);
	if (existing) return existing;

	const coordinator: RefreshCoordinator = {
		snapshot: { isRefreshing: false, generation: 0, hasValidated: false },
		listeners: new Set(),
		isRunning: false,
		runAgain: false,
		refresh: async () => undefined,
	};
	coordinator.refresh = async () => {
		if (coordinator.isRunning) {
			coordinator.runAgain = true;
			return;
		}

		coordinator.isRunning = true;
		coordinator.snapshot = {
			...coordinator.snapshot,
			isRefreshing: true,
			generation: coordinator.snapshot.generation + 1,
		};
		coordinator.listeners.forEach((listener) => listener());

		try {
			do {
				coordinator.runAgain = false;
				try {
					await refreshAuth();
				} catch {
					// AuthProvider clears the session when /me cannot be read.
				}
			} while (coordinator.runAgain);
		} finally {
			coordinator.isRunning = false;
			coordinator.snapshot = {
				...coordinator.snapshot,
				isRefreshing: false,
				hasValidated: true,
			};
			coordinator.listeners.forEach((listener) => listener());
		}
	};
	coordinators.set(refreshAuth, coordinator);
	return coordinator;
}

function subscribe(coordinator: RefreshCoordinator, listener: () => void) {
	coordinator.listeners.add(listener);
	return () => coordinator.listeners.delete(listener);
}

function useRefreshSnapshot(coordinator: RefreshCoordinator) {
	return useSyncExternalStore(
		(listener) => subscribe(coordinator, listener),
		() => coordinator.snapshot,
		() => coordinator.snapshot,
	);
}

export function useAuthRefreshState() {
	const { refreshAuth } = useAuth();
	return useRefreshSnapshot(getCoordinator(refreshAuth));
}

export default function useRefreshAuthOnFocus() {
	const { refreshAuth } = useAuth();
	const coordinator = getCoordinator(refreshAuth);
	const snapshot = useRefreshSnapshot(coordinator);

	useEffect(() => {
		const refreshWhenVisible = () => {
			if (document.visibilityState === 'visible') void coordinator.refresh();
		};

		window.addEventListener('focus', refreshWhenVisible);
		window.addEventListener('pageshow', refreshWhenVisible);
		document.addEventListener('visibilitychange', refreshWhenVisible);
		// A route may be entered while the window is already visible, without a
		// focus or visibility event. Revalidate every time an authorized data
		// entry point mounts.
		refreshWhenVisible();

		return () => {
			window.removeEventListener('focus', refreshWhenVisible);
			window.removeEventListener('pageshow', refreshWhenVisible);
			document.removeEventListener('visibilitychange', refreshWhenVisible);
		};
	}, [coordinator]);

	return snapshot;
}
