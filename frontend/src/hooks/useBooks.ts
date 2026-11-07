import { useQuery } from '@tanstack/react-query';
import { fetchBooks } from '@/api/books';
import { useAuth } from '@/contexts/useAuth';
import useRefreshAuthOnFocus from './useRefreshAuthOnFocus';

export default function useBooks() {
	const refreshState = useRefreshAuthOnFocus();
	const { user, isPending } = useAuth();
	const userId = user?.id ?? null;
	const role = user?.role ?? null;
	const {
		data: books = [],
		isLoading,
		isError,
		isFetchedAfterMount,
	} = useQuery({
		queryKey: ['books', 'list', userId, role, refreshState.generation],
		queryFn: ({ signal }) => {
			if (userId === null) throw new Error('Cannot fetch books without an authenticated user');
			return fetchBooks(userId, signal);
		},
		enabled: userId !== null && !isPending && !refreshState.isRefreshing && refreshState.hasValidated,
		refetchOnMount: 'always',
	});
	const canExposeBooks = userId !== null
		&& !isPending
		&& !refreshState.isRefreshing
		&& refreshState.hasValidated
		&& isFetchedAfterMount
		&& !isError;

	return {
		books: canExposeBooks ? books : [],
		isLoading: userId !== null && (isPending || refreshState.isRefreshing || !refreshState.hasValidated || (isLoading || (!isFetchedAfterMount && !isError))),
		isError: userId !== null && !refreshState.isRefreshing && refreshState.hasValidated && isError,
	};
}
