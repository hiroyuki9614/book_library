import PdfReader from '@/features/components/PdfReader';
import useBook from '@/hooks/useBook';
import { useParams } from 'react-router-dom';

export default function ReaderPage() {
	const { id } = useParams();
	const bookId = Number(id);
	const { book, isLoading, error } = useBook(bookId);

	if (isLoading) {
		return <p>書籍を読み込んでいます。</p>;
	}

	if (error || !book) {
		return <p>書籍が見つかりません。</p>;
	}

	if (book.fileType !== 'pdf') {
		return <p>現在はPDF書籍のみ閲覧できます。</p>;
	}

	return <PdfReader bookId={book.id} />;
}
