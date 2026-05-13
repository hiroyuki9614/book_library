import * as React from 'react';
import { Link } from 'react-router-dom';

import { Sidebar, SidebarContent, SidebarGroup, SidebarGroupContent, SidebarGroupLabel, SidebarMenu, SidebarMenuButton, SidebarMenuItem, SidebarRail } from '@/components/ui/sidebar';

type MenuGroup = {
	title: string;
	url: string;
	className?: string;
	items: {
		title: string;
		url: string;
		isActive?: boolean;
	}[];
};

const data: {
	versions: string[];
	navMain: MenuGroup[];
	adminMenu: MenuGroup[];
} = {
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
			title: 'BeeLib',
			url: '#',
			className: 'mt-auto',

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

const renderMenu = (menu: MenuGroup[]) =>
	menu.map((group) => (
		<SidebarGroup key={group.title} className={group.className}>
			<SidebarGroupLabel>{group.title}</SidebarGroupLabel>

			<SidebarGroupContent>
				<SidebarMenu>
					{group.items.map((item) => (
						<SidebarMenuItem key={item.title}>
							<SidebarMenuButton asChild isActive={item.isActive}>
								<Link to={item.url}>{item.title}</Link>
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
			{/* <SidebarHeader /> */}

			<SidebarContent>
				{renderMenu(data.navMain)}

				<div className='mt-auto mb-4'>{renderMenu(data.adminMenu)}</div>
			</SidebarContent>

			<SidebarRail />
		</Sidebar>
	);
}
