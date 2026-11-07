import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchBook, type BookDetails } from '@/api/books';
import { useAuth } from '@/contexts/useAuth';
import useRefreshAuthOnFocus from './useRefreshAuthOnFocus';

type BookEntry = {
	userId: number | null;
	role: string | null;
	id: number;
	book: BookDetails | null;
};

export default function useBook(id: number) {
	const refreshState = useRefreshAuthOnFocus();
	const { user, isPending } = useAuth();
	const userId = user?.id ?? null;
	const role = user?.role ?? null;
	const validId = Number.isSafeInteger(id) && id > 0;
	const [entry, setEntry] = useState<BookEntry>({ userId, role, id, book: null });
	const query = useQuery({
		queryKey: ['books', 'detail', userId, role, id],
		queryFn: () => fetchBook(id),
		enabled: validId && userId !== null && !isPending && !refreshState.isRefreshing && refreshState.hasValidated,
		refetchOnMount: 'always',
		retry: false,
	});

	// A new route or identity can use cached metadata only after its own fresh
	// authorization request. An already-open same-account reader keeps its
	// accepted entry during later focus refreshes, including a permission loss.
	const verifiedBook = userId !== null && query.isSuccess && query.isFetchedAfterMount ? query.data : null;
	if (!Object.is(entry.userId, userId) || !Object.is(entry.role, role) || !Object.is(entry.id, id)) {
		setEntry({ userId, role, id, book: verifiedBook ?? null });
	} else if (verifiedBook && entry.book !== verifiedBook) {
		setEntry({ userId, role, id, book: verifiedBook });
	}

	const book = userId !== null && Object.is(entry.userId, userId) && Object.is(entry.role, role) && Object.is(entry.id, id)
		? entry.book
		: null;
	const error = userId === null || book ? null : query.error;
	const isLoading = validId && !book && (isPending
		|| refreshState.isRefreshing
		|| !refreshState.hasValidated
		|| (userId !== null && !error));

	return { book, isLoading, error };
}
