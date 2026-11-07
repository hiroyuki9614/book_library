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
		queryFn: ({ signal }) => {
			if (userId === null) throw new Error('Cannot fetch books without an authenticated user');
			return fetchBooks(userId, signal);
		},
		enabled: userId !== null,
	});

	return {
		books: userId === null ? [] : books,
		isLoading: isPending || isLoading,
		isError: userId !== null && isError,
	};
}
