import { createBrowserRouter } from 'react-router-dom';
import AppLayout from '@/layouts/AppLayout';
import Home from '@/pages/Home';
import About from '@/pages/About';
import Contact from '@/pages/Contact';
import ReaderPage from '@/pages/ReaderPage';
import ReaderPageT from '@/pages/ReaderPageT';
export const appRouter = createBrowserRouter([
	{
		path: '/',
		element: <AppLayout />,
		children: [
			{ index: true, element: <Home /> },
			{ path: 'about', element: <About /> },
			{ path: 'contact', element: <Contact /> },
			{ path: 'reader/:id', element: <ReaderPage /> },
			{ path: 'reader-t/', element: <ReaderPageT /> },
		],
	},
]);
