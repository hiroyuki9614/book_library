import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import SideNav from '@/components/SideNav';

createRoot(document.getElementById('root')!).render(
	<StrictMode>
		<SideNav />
	</StrictMode>,
);
