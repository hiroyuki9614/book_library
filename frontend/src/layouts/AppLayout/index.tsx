import { Outlet } from 'react-router-dom';
import { AppSidebar } from '@/components/SideNav';
import { SidebarInset, SidebarProvider } from '@/components/ui/sidebar';

export default function AppLayout() {
	return (
		<SidebarProvider>
			<AppSidebar />
			<SidebarInset>
				<main className='min-h-screen'>
					<Outlet />
				</main>
			</SidebarInset>
		</SidebarProvider>
	);
}
