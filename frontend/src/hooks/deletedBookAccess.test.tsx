import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook } from 'vitest-browser-react';
import { afterEach, expect, test, vi } from 'vitest';
import type { ReactNode } from 'react';
const m = vi.hoisted(() => ({ fetchBook: vi.fn(), refreshAuth: vi.fn() }));
vi.mock('@/api/books', () => ({ fetchBook: m.fetchBook }));
// The reader hook now requires a signed-in identity; keep these permission checks authenticated.
vi.mock('@/contexts/useAuth', () => ({
 useAuth: () => ({ user: { id: 7, role: 'user' }, isPending: false, refreshAuth: m.refreshAuth }),
}));
import useBook from './useBook';
afterEach(() => vi.resetAllMocks());
test('a cached book is not readable while its current permission is being checked', async () => {
 const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
 client.setQueryData(['books', 'detail', 7, 'user', 8], { id: 8, title: 'Previously readable', fileType: 'pdf' });
 let reject!: (e: Error) => void;
 m.fetchBook.mockImplementation(() => new Promise((_resolve, fail) => { reject = fail; }));
 const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
 const { result } = await renderHook(() => useBook(8), { wrapper });
 await expect.poll(() => m.fetchBook.mock.calls.length).toBeGreaterThan(0);
 expect(result.current.isLoading).toBe(true);
 reject(new Error('API request failed: 404'));
 await expect.poll(() => Boolean(result.current.error)).toBe(true);
 expect(result.current.book).toBeNull();
});

test('an authorized open reader survives a later background permission rejection', async () => {
 const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
 const book = { id: 8, title: 'Open publication', fileType: 'pdf' };
 m.fetchBook.mockResolvedValueOnce(book);
 const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
 const { result } = await renderHook(() => useBook(8), { wrapper });
 await expect.poll(() => result.current.isLoading).toBe(false);
 expect(result.current.book).toEqual(book);
 m.fetchBook.mockRejectedValue(new Error('API request failed: 404'));
 await client.invalidateQueries({ queryKey: ['books', 'detail', 7, 'user', 8] });
 expect(result.current.book).toEqual(book);
 expect(result.current.isLoading).toBe(false);
 expect(result.current.error).toBeNull();
});

test('changing book ids clears the previous entry even when returning before the next request finishes', async () => {
 const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
 m.fetchBook.mockResolvedValueOnce({ id: 8, title: 'Previously open' });
 const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
 const hook = await renderHook((id: number = 8) => useBook(id), { wrapper, initialProps: 8 });
 await expect.poll(() => hook.result.current.isLoading).toBe(false);
 m.fetchBook.mockImplementation(() => new Promise(() => {}));
 await hook.rerender(9);
 expect(hook.result.current.book).toBeNull();
 await hook.rerender(8);
 expect(hook.result.current.book).toBeNull();
 expect(hook.result.current.isLoading).toBe(true);
});
