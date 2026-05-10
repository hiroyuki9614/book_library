import GridLayout from '@/layouts/GridLayout';
import { AppSidebar } from '@/components/SideNav';
import { Link } from 'react-router-dom';

function App() {
	return (
		<GridLayout>
			<AppSidebar />
			<div className='flex h-screen items-center justify-center gap-4'>
				<Link to='/about'>about</Link>
				<Link to='/contact'>contact</Link>
			</div>
		</GridLayout>
	);
}

export default App;
