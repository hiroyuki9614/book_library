import { useEffect } from 'react';
import { useAuth } from '@/contexts/useAuth';

export default function useRefreshAuthOnFocus() {
	const { isPending, refreshAuth } = useAuth();

	useEffect(() => {
		if (isPending) return;

		let refreshInFlight = false;
		const refreshSession = () => {
			if (document.visibilityState !== 'visible' || refreshInFlight) return;

			refreshInFlight = true;
			void refreshAuth()
				.catch(() => undefined)
				.finally(() => {
					refreshInFlight = false;
				});
		};
		const handleVisibilityChange = () => {
			if (document.visibilityState === 'visible') refreshSession();
		};

		window.addEventListener('focus', refreshSession);
		document.addEventListener('visibilitychange', handleVisibilityChange);
		return () => {
			window.removeEventListener('focus', refreshSession);
			document.removeEventListener('visibilitychange', handleVisibilityChange);
		};
	}, [isPending, refreshAuth]);
}
