import { useAuth } from '@/contexts/useAuth';
import { Navigate, Outlet } from 'react-router-dom';

const RequireAuth = () => {
	const { isAuthenticated, isPending } = useAuth();

	if (isPending) {
		return <div>Loading...</div>;
	}

	if (!isAuthenticated) {
		return <Navigate to='/login' replace />;
	}

	return <Outlet />;
};

export default RequireAuth;
