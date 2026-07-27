import { Navigate, Outlet } from 'react-router-dom';
import { useAuth } from '@/contexts/useAuth';

export default function GuestRoute() {
	const { isAuthenticated, isPending } = useAuth();

	if (isPending) {
		return <div>Loading...</div>;
	}

	if (isAuthenticated) {
		return <Navigate to='/' replace />;
	}

	return <Outlet />;
}
