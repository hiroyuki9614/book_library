import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import EpubReader from '../../src/features/components/EpubReader';
import PdfReader from '../../src/features/components/PdfReader';
import '../../src/index.css';

const query = new URLSearchParams(location.search);
const direction = query.get('direction') === 'rtl' ? 'rtl' : 'ltr';
const reader = query.get('kind') === 'pdf'
	? <PdfReader bookId={1} pageTurnDirection={direction} />
	: <EpubReader bookId={1} pageTurnDirection={direction} />;

createRoot(document.getElementById('root')!).render(<BrowserRouter>{reader}</BrowserRouter>);
