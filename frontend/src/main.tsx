import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import './index.css';
// Development-only health check: imported only in dev mode and when API base is configured.
if (import.meta.env.MODE === 'development' && import.meta.env.VITE_API_BASE_URL) {
	// eslint-disable-next-line @typescript-eslint/no-floating-promises
	import('./dev/health-check');
}
createRoot(document.getElementById('root')!).render(
	<StrictMode>
		<App />
	</StrictMode>,
);
