import { useQuery } from '@tanstack/react-query';
import { fetchBooks } from '@/api/books';
import { useAuth } from '@/contexts/useAuth';

export default function useBooks() {
	const { user, isPending } = useAuth();
	const userId = isPending ? null : user?.id ?? null;
	const {
		data: books = [],
		isLoading,
		isError,
	} = useQuery({
		queryKey: ['books', 'list', userId],
		queryFn: () => fetchBooks(),
		enabled: userId !== null,
	});

	return {
		books: userId === null ? [] : books,
		isLoading: isPending || isLoading,
		isError: userId !== null && isError,
	};
}
