import { useAuth } from '@/contexts/useAuth';
import { Navigate, Outlet } from 'react-router-dom';

const RequireAuth = () => {
	const { isPending, isAuthenticated } = useAuth();

	if (isPending) {
		return <div>Loading...</div>;
	}

	if (!isAuthenticated) {
		return <Navigate to='/login' replace />;
	}

	return <Outlet />;
};

export default RequireAuth;
