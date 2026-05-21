import { Outlet } from 'react-router-dom';
import { AppSidebar } from '@/components/SideNav';
import { SidebarInset, SidebarProvider } from '@/components/ui/sidebar';

export default function AppLayout() {
	return (
		<SidebarProvider>
			<AppSidebar />
			<SidebarInset>
				<section className='min-h-screen'>
					<Outlet />
				</section>
			</SidebarInset>
		</SidebarProvider>
	);
}
