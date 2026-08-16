import { useQuery } from '@tanstack/react-query';
import { fetchBooks } from '@/api/books';

export default function useBooks() {
	const {
		data: books = [],
		isLoading,
		isError,
	} = useQuery({
		queryKey: ['books'],
		queryFn: () => fetchBooks(),
	});

	return { books, isLoading, isError };
}
