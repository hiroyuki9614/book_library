import { RouterProvider } from 'react-router-dom';
import { appRouter } from './routes/AppRoutes';
import { AuthProvider } from '@/contexts/AuthProvider';
import { Toaster } from '@/components/ui/sonner';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const queryClient = new QueryClient();

function App() {
	return (
		<QueryClientProvider client={queryClient}>
			<AuthProvider>
				<RouterProvider router={appRouter} />
				<Toaster />
			</AuthProvider>
		</QueryClientProvider>
	);
}

export default App;
