import { createBrowserRouter, Navigate } from 'react-router-dom';

import AppLayout from '@/layouts/AppLayout';

import RequireAuth from '@/components/RequireAuth';

import Login from '@/pages/Login';
import Home from '@/pages/Home';
import About from '@/pages/About';
import Contact from '@/pages/Contact';
import ReaderPage from '@/pages/ReaderPage';
import ReaderPageT from '@/pages/ReaderPageT';
import NotFound from '@/pages/404';

export const appRouter = createBrowserRouter([
	{
		path: '/login',
		element: <Login />,
	},
	{
		element: <RequireAuth />,
		children: [
			{
				path: '/',
				element: <AppLayout />,
				children: [
					{ index: true, element: <Home /> },
					{ path: 'about', element: <About /> },
					{ path: 'contact', element: <Contact /> },
					{ path: 'reader/:id', element: <ReaderPage /> },
					{ path: 'reader-t', element: <ReaderPageT /> },
					{ path: '*', element: <NotFound /> },
				],
			},
		],
	},
]);
