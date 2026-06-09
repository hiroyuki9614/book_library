import { Outlet } from 'react-router-dom';
import { Menu } from 'lucide-react';
import { AppSidebar } from '@/components/SideNav';
import { SidebarInset, SidebarProvider, SidebarTrigger } from '@/components/ui/sidebar';

export default function AppLayout() {
	return (
		<SidebarProvider>
			<AppSidebar collapsible='offcanvas' />

			<SidebarInset>
				<header className='sticky top-0 z-10 flex h-14 items-center px-4 md:hidden'>
					<SidebarTrigger>
						<Menu className='h-5 w-5' />
					</SidebarTrigger>
				</header>

				<section className='min-h-screen'>
					<Outlet />
				</section>
			</SidebarInset>
		</SidebarProvider>
	);
}
