import EpubReader from '@/features/components/EpubReader';
import PdfReader from '@/features/components/PdfReader';
import { useParams } from 'react-router-dom';

export default function ReaderPageT() {
	const { id } = useParams();

	const books = [
		{ id: 1, title: 'Epub Book', type: 'epub' },
		{ id: 2, title: 'PDF Book', type: 'pdf' },
	];

	const book = books.find((book) => book.id === Number(id));

	if (!book) {
		return <p>書籍が見つかりません。</p>;
	}

	return <>{book.type === 'epub' ? <EpubReader /> : <PdfReader />}</>;
}
