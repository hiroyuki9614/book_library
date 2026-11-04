import { useQuery } from '@tanstack/react-query';
import { fetchBook } from '@/api/books';

export default function useBook(id: number) {
	const {
		data: book = null,
		isLoading,
		isFetching,
		error,
	} = useQuery({
		queryKey: ['books', id],
		queryFn: () => fetchBook(id),
		enabled: Number.isSafeInteger(id) && id > 0,
		refetchOnMount: 'always',
		retry: false,
	});

	return { book: error || isFetching ? null : book, isLoading: isLoading || isFetching, error };
}
