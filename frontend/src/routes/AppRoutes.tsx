import { createBrowserRouter } from 'react-router-dom';

import AppLayout from '@/layouts/AppLayout';

import Home from '@/pages/Home';
import About from '@/pages/About';
import Contact from '@/pages/Contact';

export const appRouter = createBrowserRouter([
	{
		path: '/',
		element: <AppLayout />,
		children: [
			{ index: true, element: <Home /> },
			{ path: 'about', element: <About /> },
			{ path: 'contact', element: <Contact /> },
		],
	},
]);
