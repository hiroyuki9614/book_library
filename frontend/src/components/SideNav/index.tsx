import * as React from 'react';
import { Link } from 'react-router-dom';
import { Sidebar, SidebarContent, SidebarGroup, SidebarGroupContent, SidebarGroupLabel, SidebarMenu, SidebarMenuButton, SidebarMenuItem, SidebarRail, SidebarHeader } from '@/components/ui/sidebar';
import { useAuth } from '@/contexts/useAuth';
import { navItems } from './navItems';
import type { MenuGroup } from './navItems';

export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
	const { logout, role } = useAuth();
	const renderMenu = (menu: MenuGroup[]) =>
		menu.map((group) => (
			<SidebarGroup key={group.title} className={group.className}>
				<SidebarGroupLabel>{group.title}</SidebarGroupLabel>
				<SidebarGroupContent>
					<SidebarMenu>
						{group.items.map((item) => (
							<SidebarMenuItem key={item.title}>
								<SidebarMenuButton asChild isActive={item.isActive}>
									{item.url ? (
										<Link to={item.url}>{item.title}</Link>
									) : (
										<button className='cursor-pointer' onClick={item.action === 'logout' ? logout : undefined}>
											{item.title}
										</button>
									)}
								</SidebarMenuButton>
							</SidebarMenuItem>
						))}
					</SidebarMenu>
				</SidebarGroupContent>
			</SidebarGroup>
		));
	return (
		<Sidebar {...props}>
			<SidebarHeader>ログイン状態： {role ?? '未ログイン'}</SidebarHeader>
			<SidebarContent>
				{renderMenu(navItems.navMain)}

				<div className='mt-auto mb-4'>{renderMenu(navItems.adminMenu)}</div>
			</SidebarContent>
			<SidebarRail />
		</Sidebar>
	);
}
