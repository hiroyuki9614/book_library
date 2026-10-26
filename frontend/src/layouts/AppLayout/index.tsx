import { Outlet, useLocation } from 'react-router-dom';
import { Menu } from 'lucide-react';
import { AppSidebar } from '@/components/SideNav';
import { SidebarInset, SidebarProvider, SidebarTrigger } from '@/components/ui/sidebar';

export default function AppLayout() {
	const { pathname } = useLocation();
	const isReaderRoute = pathname.startsWith('/reader/');

	return (
		<SidebarProvider>
			<AppSidebar collapsible='offcanvas' />

			<SidebarInset>
				{!isReaderRoute && (
					<header className='sticky top-0 z-10 flex h-14 shrink-0 items-center px-4 md:hidden'>
						<SidebarTrigger className='size-11'>
							<Menu className='h-5 w-5' />
						</SidebarTrigger>
					</header>
				)}

				<section className='min-h-0 flex-1'>
					<Outlet />
				</section>
			</SidebarInset>
		</SidebarProvider>
	);
}
