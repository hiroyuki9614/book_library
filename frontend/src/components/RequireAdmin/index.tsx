import { Navigate, Outlet } from 'react-router-dom';
import { useAuth } from '@/contexts/useAuth';

export default function RequireAdmin() {
	const { role } = useAuth();

	if (role !== 'admin') {
		return <Navigate to='/' replace />;
	}

	return <Outlet />;
}
