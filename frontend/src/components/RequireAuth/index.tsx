import { Navigate, useOutlet } from 'react-router-dom';
import { useAuth } from '@/contexts/useAuth';
import useRefreshAuthOnFocus from '@/hooks/useRefreshAuthOnFocus';

const RequireAuth = () => {
	const { isAuthenticated, isPending } = useAuth();
	const refreshState = useRefreshAuthOnFocus(isAuthenticated);
	const outlet = useOutlet();
	const isIdentityUncertain = isPending
		|| refreshState.isRefreshing
		|| (isAuthenticated && !refreshState.hasValidated);

	if (!isAuthenticated) {
		if (isPending) return <div>Loading...</div>;
		return <Navigate to='/login' replace />;
	}

	return (
		<div className='relative min-h-screen'>
			<div
				aria-hidden={isIdentityUncertain ? 'true' : undefined}
				inert={isIdentityUncertain}
				style={isIdentityUncertain ? { visibility: 'hidden' } : undefined}
			>
				{outlet}
			</div>
			{isIdentityUncertain && (
				<div role='status' aria-live='polite' className='fixed inset-0 z-[100] grid place-items-center bg-background text-foreground'>
					セッションを確認しています。
				</div>
			)}
		</div>
	);
};

export default RequireAuth;
