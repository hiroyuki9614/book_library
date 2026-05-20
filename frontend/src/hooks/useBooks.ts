import { useQuery } from '@tanstack/react-query';
import booksApiMock from '@/mocks/booksApiMock ';

export default function useBooks() {
	const {
		data: books = [],
		isLoading,
		error,
	} = useQuery({
		queryKey: ['books'],
		queryFn: () => booksApiMock(),
	});

	return { books, isLoading, error };
}
