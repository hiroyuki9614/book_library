import { AppSidebar } from '@/components/SideNav';
import './style.css';

type Props = {
	children: React.ReactNode;
};

export default function GridLayout({ children }: Props) {
	return (
		<main className='grid grid-cols-[repeat(12,_1fr)] grid-rows-[auto_1fr] h-screen gap-4'>
			<AppSidebar />
			{children}
		</main>
	);
}
