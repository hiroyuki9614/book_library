import { useQuery } from '@tanstack/react-query';
import booksApiMock from '@/mocks/booksApiMock';

export default function useBook(id: number) {
	const {
		data: books = [],
		isLoading,
		error,
	} = useQuery({
		queryKey: ['books', id],
		queryFn: () => booksApiMock(id),
	});

	return { books, isLoading, error };
}
