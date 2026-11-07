import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchBook, type BookDetails } from '@/api/books';
import { useAuth } from '@/contexts/useAuth';
import useRefreshAuthOnFocus from './useRefreshAuthOnFocus';

export default function useBook(id: number) {
 useRefreshAuthOnFocus();
 const { user, isPending } = useAuth();
 const userId = isPending ? null : user?.id ?? null;
 const validId = Number.isSafeInteger(id) && id > 0;
 const [entry, setEntry] = useState<{ userId: number | null; id: number; book: BookDetails | null }>({ userId, id, book: null });
 const query = useQuery({
  queryKey: ['books', 'detail', userId, id],
  queryFn: () => fetchBook(id),
  enabled: validId && userId !== null,
  refetchOnMount: 'always',
  retry: false,
 });
 // A fresh successful response authorizes this entry, never cached metadata.
 // Retain the accepted document during later focus/reconnect refetches.
 const verifiedBook = userId !== null && query.isSuccess && query.isFetchedAfterMount ? query.data : null;
 if (!Object.is(entry.userId, userId) || !Object.is(entry.id, id)) {
  setEntry({ userId, id, book: verifiedBook ?? null });
 } else if (verifiedBook && entry.book !== verifiedBook) {
  setEntry({ userId, id, book: verifiedBook });
 }
 const book = userId !== null && Object.is(entry.userId, userId) && Object.is(entry.id, id) ? entry.book : null;
 const error = userId === null || book ? null : query.error;
 return { book, isLoading: validId && (isPending || (userId !== null && !book && !error)), error };
}
