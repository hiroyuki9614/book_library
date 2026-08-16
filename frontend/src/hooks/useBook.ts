import { useQuery } from '@tanstack/react-query';
import { fetchBook } from '@/api/books';

export default function useBook(id: number) {
	const {
		data: book = null,
		isLoading,
		error,
	} = useQuery({
		queryKey: ['books', id],
		queryFn: () => fetchBook(id),
		enabled: Number.isSafeInteger(id),
	});

	return { book, isLoading, error };
}
