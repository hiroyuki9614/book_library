import * as React from 'react';
import { Sidebar, SidebarContent, SidebarGroup, SidebarGroupContent, SidebarGroupLabel, SidebarHeader, SidebarMenu, SidebarMenuButton, SidebarMenuItem, SidebarRail } from '@/components/ui/sidebar';
const data = {
	versions: ['1.0.1', '1.1.0-alpha', '2.0.0-beta1'],
	navMain: [
		{
			title: 'BeeLib',
			url: '#',
			items: [
				{
					title: 'Dashboard',
					url: '/',
					isActive: true,
				},
				{
					title: '本棚',
					url: '#',
				},
				{
					title: '読書中',
					url: '#',
				},
				{
					title: 'お気に入り',
					url: '#',
				},
			],
		},
	],
	adminMenu: [
		{
			items: [
				{
					title: '管理画面',
					url: '#',
				},
				{
					title: 'マイページ',
					url: '#',
				},
				{
					title: 'ログアウト',
					url: '#',
				},
			],
		},
	],
};

const renderMenu = (menu: typeof data.navMain) =>
	menu.map((item) => (
		<SidebarGroup key={item.title}>
			<SidebarGroupLabel>{item.title}</SidebarGroupLabel>
			<SidebarGroupContent>
				<SidebarMenu>
					{item.items.map((item) => (
						<SidebarMenuItem key={item.title}>
							<SidebarMenuButton asChild isActive={item.isActive}>
								<a href={item.url}>{item.title}</a>
							</SidebarMenuButton>
						</SidebarMenuItem>
					))}
				</SidebarMenu>
			</SidebarGroupContent>
		</SidebarGroup>
	));
export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
	return (
		<Sidebar {...props}>
			{/* <SidebarHeader></SidebarHeader> */}
			<SidebarContent>
				{renderMenu(data.navMain)}

				<div className='mt-6'>{renderMenu(data.adminMenu)}</div>
			</SidebarContent>
			<SidebarRail />
		</Sidebar>
	);
}
