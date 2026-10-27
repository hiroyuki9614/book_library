import { createRoot } from 'react-dom/client';
import { BrowserRouter, Routes, Route, Link } from 'react-router-dom';
import EpubReader from '../../src/features/components/EpubReader';
import PdfReader from '../../src/features/components/PdfReader';
import '../../src/index.css';
createRoot(document.getElementById('root')!).render(
 <BrowserRouter><Routes>
  <Route path='/e2e/readers/harness.html' element={new URLSearchParams(location.search).get('kind') === 'pdf' ? <PdfReader bookId={1} /> : <EpubReader bookId={1} />} />
  <Route path='/' element={<Link to='/e2e/readers/harness.html'>テスト書籍を読む</Link>} />
 </Routes></BrowserRouter>
);
