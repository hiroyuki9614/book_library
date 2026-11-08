import { createRoot } from 'react-dom/client';
import { BrowserRouter, Routes, Route, Link, useParams } from 'react-router-dom';
import EpubReader from '../../src/features/components/EpubReader';
import PdfReader from '../../src/features/components/PdfReader';
import { BookTable } from '../../src/components/BookTable';
import type { Book } from '../../src/data/booksData';
import '../../src/index.css';
const params = new URLSearchParams(location.search);
const direction = params.get('direction') === 'rtl' ? 'rtl' : params.get('direction') === 'ltr' ? 'ltr' : undefined;

const shelfBooks: Book[] = [
 { id: 1, title: 'Measured shelf book', author: 'Fixture author one', category: 'Frontend', status: 'Reading', fileType: 'pdf', currentPage: 34, progressPercentage: 34.4 },
 { id: 9, title: 'Unmeasured shelf book', author: 'Fixture author nine', category: 'Architecture', status: 'Completed', fileType: 'epub', currentPage: 0, progressPercentage: null },
];

export function ShelfNavigationTarget() {
 const { bookId } = useParams();
 return <main data-testid='shelf-navigation-target' data-book-id={bookId}>Synthetic shelf destination: book {bookId}</main>;
}

createRoot(document.getElementById('root')!).render(
 <BrowserRouter><Routes>
  <Route path='/e2e/readers/harness.html' element={params.get('kind') === 'shelf' ? <BookTable books={shelfBooks} itemsPerPage={10} searchQuery='' sort='oldest' isLoading={false} status='all' /> : params.get('kind') === 'pdf' ? <PdfReader bookId={1} pageTurnDirection={direction} /> : <EpubReader bookId={1} pageTurnDirection={direction} />} />
  <Route path='/reader/:bookId' element={params.get('kind') === 'shelf' ? <ShelfNavigationTarget /> : null} />
  <Route path='/' element={<Link to='/e2e/readers/harness.html'>テスト書籍を読む</Link>} />
 </Routes></BrowserRouter>
);
