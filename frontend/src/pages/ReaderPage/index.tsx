import EpubReader from '@/features/components/EpubReader';
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

	if (book.fileType === 'epub') {
		return <EpubReader bookId={book.id} />;
	}

	if (book.fileType === 'pdf') {
		return <PdfReader bookId={book.id} />;
	}

	return <p>この書籍ファイル形式は閲覧できません。</p>;
}
