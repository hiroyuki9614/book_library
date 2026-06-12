import { Navigate, Outlet } from 'react-router-dom';
import { useAuth } from '@/contexts/useAuth';

export default function GuestRoute() {
	const { role } = useAuth();

	if (role) {
		return <Navigate to='/' replace />;
	}

	return <Outlet />;
}
