import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchBook, type BookDetails } from '@/api/books';

export default function useBook(id: number) {
 const validId = Number.isSafeInteger(id) && id > 0;
 const [entry, setEntry] = useState<{ id: number; book: BookDetails | null }>({ id, book: null });
 const query = useQuery({
  queryKey: ['books', id],
  queryFn: () => fetchBook(id),
  enabled: validId,
  refetchOnMount: 'always',
  retry: false,
 });
 // A fresh successful response authorizes this entry, never cached metadata.
 // Retain the accepted document during later focus/reconnect refetches.
 const verifiedBook = query.isSuccess && query.isFetchedAfterMount ? query.data : null;
 if (!Object.is(entry.id, id)) {
  setEntry({ id, book: verifiedBook ?? null });
 } else if (verifiedBook && entry.book !== verifiedBook) {
  setEntry({ id, book: verifiedBook });
 }
 const book = Object.is(entry.id, id) ? entry.book : null;
 const error = book ? null : query.error;
 return { book, isLoading: validId && !book && !error, error };
}
