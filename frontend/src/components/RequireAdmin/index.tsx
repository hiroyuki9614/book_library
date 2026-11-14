import { Navigate, useOutlet } from 'react-router-dom';
import { useAuth } from '@/contexts/useAuth';

export default function RequireAdmin() {
	const { user, role, isPending } = useAuth();
	const outlet = useOutlet();

	if (isPending) {
		return <div>Loading...</div>;
	}

	if (role !== 'admin') {
		return <Navigate to='/' replace />;
	}

	return <div key={`${user?.id ?? ''}:${role}`} style={{ display: 'contents' }}>{outlet}</div>;
}
