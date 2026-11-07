import { Navigate, useOutlet } from 'react-router-dom';
import { useAuth } from '@/contexts/useAuth';

export default function RequireAdmin() {
	const { user, role, isPending } = useAuth();
	const outlet = useOutlet();

	if (isPending && !user) {
		return <div>Loading...</div>;
	}

	if (!isPending && role !== 'admin') {
		return <Navigate to='/' replace />;
	}

	return (
		<div
			key={`${user?.id ?? ''}:${role}`}
			aria-hidden={isPending ? 'true' : undefined}
			inert={isPending}
			style={isPending ? { display: 'contents', visibility: 'hidden' } : { display: 'contents' }}
		>
			{outlet}
		</div>
	);
}
