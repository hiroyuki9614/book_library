import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchBook, type BookDetails } from '@/api/books';
import { useAuth } from '@/contexts/useAuth';
import { useRefreshAuthOnMount } from './useRefreshAuthOnFocus';

type BookEntry = {
	userId: number | null;
	id: number;
	book: BookDetails | null;
};

export default function useBook(id: number) {
	const refreshState = useRefreshAuthOnMount();
	const { user, isPending } = useAuth();
	const userId = user?.id ?? null;
	const role = user?.role ?? null;
	const validId = Number.isSafeInteger(id) && id > 0;
	const [entry, setEntry] = useState<BookEntry>({ userId, id, book: null });
	const query = useQuery({
		queryKey: ['books', 'detail', userId, role, id],
		queryFn: () => fetchBook(id),
		enabled: validId && userId !== null && !isPending && !refreshState.isRefreshing && refreshState.hasValidated,
		refetchOnMount: 'always',
		retry: false,
	});

	// A new route or identity can use cached metadata only after its own fresh
	// authorization request. An already-open same-account reader keeps its
	// accepted entry during later focus refreshes or a same-user role downgrade.\n\t// A new reader mount has no accepted entry and must pass fresh authorization.
	const verifiedBook = userId !== null && query.isSuccess && query.isFetchedAfterMount ? query.data : null;
	if (!Object.is(entry.userId, userId) || !Object.is(entry.id, id)) {
		setEntry({ userId, id, book: verifiedBook ?? null });
	} else if (verifiedBook && entry.book !== verifiedBook) {
		setEntry({ userId, id, book: verifiedBook });
	}

	const book = userId !== null && Object.is(entry.userId, userId) && Object.is(entry.id, id)
		? entry.book
		: null;
	const error = userId === null || book ? null : query.error;
	const isLoading = validId && !book && (isPending
		|| refreshState.isRefreshing
		|| !refreshState.hasValidated
		|| (userId !== null && !error));

	return { book, isLoading, error };
}
