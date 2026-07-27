import { createBrowserRouter } from 'react-router-dom';
import GuestRoute from '@/components/GuestRoute';
import AppLayout from '@/layouts/AppLayout';
import RequireAuth from '@/components/RequireAuth';

import Login from '@/pages/Login';
import Home from '@/pages/Home';
import About from '@/pages/About';
import Contact from '@/pages/Contact';
import ReaderPage from '@/pages/ReaderPage';
import Admin from '@/pages/Admin';
import NewUserPage from '@/pages/Admin/Users/New';
import RequireAdmin from '@/components/RequireAdmin';
import NotFound from '@/pages/404';

export const appRouter = createBrowserRouter([
	{
		element: <GuestRoute />,
		children: [
			{
				path: '/login',
				element: <Login />,
			},
		],
	},
	{
		path: '/admin',
		element: <AppLayout />,
		children: [
			{ index: true, element: <Admin /> },
			{
				path: 'users',
				element: <RequireAdmin />,
				children: [{ path: 'new', element: <NewUserPage /> }],
			},
		],
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
					{ path: '*', element: <NotFound /> },
				],
			},
		],
	},
]);
