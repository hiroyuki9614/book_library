import { RouterProvider } from 'react-router-dom';
import { appRouter } from './routes/AppRoutes';
import { AuthProvider } from '@/contexts/AuthProvider';
import { Toaster } from '@/components/ui/sonner';

function App() {
	return (
		<AuthProvider>
			<RouterProvider router={appRouter} />
			<Toaster />
		</AuthProvider>
	);
}

export default App;
