import { Hono } from 'hono';
import { beforeEach, expect, test, vi } from 'vitest';
const m = vi.hoisted(() => ({
 session: vi.fn(), user: vi.fn(), find: vi.fn(), removeFile: vi.fn(), access: vi.fn(),
 lock: vi.fn(), readings: vi.fn(), permissions: vi.fn(), files: vi.fn(), book: vi.fn(),
}));
vi.mock('../../lib/auth.js', () => ({ auth: { api: { getSession: m.session } } }));
vi.mock('../../lib/bookStorage.js', () => ({ deleteStoredBookFile: m.removeFile }));
import routes from './permanentDeleteRoutes.js';
const tx = {
 $queryRawUnsafe: m.lock, book: { findUnique: m.find, delete: m.book },
 readingInfo: { deleteMany: m.readings }, roleBookPermission: { deleteMany: m.permissions },
 bookFile: { deleteMany: m.files },
};
const app = new Hono();
app.use('*', async (c, next) => {
 c.set('prisma' as never, { user: { findFirst: m.user }, $transaction: async (fn: (v: typeof tx) => unknown) => fn(tx) } as never);
 await next();
});
app.route('/', routes);
beforeEach(() => {
 vi.resetAllMocks();
 m.session.mockResolvedValue({ user: { id: '1' } });
 m.user.mockResolvedValue({ id: 1, role: { name: 'admin' } });
 m.find.mockResolvedValue({ id: 8, deletedAt: new Date(), bookFiles: [{ fileUrl: 'books/private.pdf' }] });
});
test('anonymous and general users cannot permanently delete', async () => {
 m.session.mockResolvedValueOnce(null);
 expect((await app.request('/books/8', { method: 'DELETE' })).status).toBe(401);
 m.user.mockResolvedValueOnce({ role: { name: 'user' } });
 expect((await app.request('/books/8', { method: 'DELETE' })).status).toBe(403);
 expect(m.removeFile).not.toHaveBeenCalled();
});
test('active books cannot be permanently deleted', async () => {
 m.find.mockResolvedValueOnce({ id: 8, deletedAt: null, bookFiles: [] });
 expect((await app.request('/books/8', { method: 'DELETE' })).status).toBe(409);
 expect(m.removeFile).not.toHaveBeenCalled();
});
test('missing and malformed books return 404', async () => {
 expect((await app.request('/books/0', { method: 'DELETE' })).status).toBe(404);
 m.find.mockResolvedValueOnce(null);
 expect((await app.request('/books/8', { method: 'DELETE' })).status).toBe(404);
});
test('storage deletion succeeds before any database deletion', async () => {
 m.removeFile.mockImplementation(async () => { expect(m.book).not.toHaveBeenCalled(); expect(m.files).not.toHaveBeenCalled(); });
 expect((await app.request('/books/8', { method: 'DELETE' })).status).toBe(200);
 expect(m.lock).toHaveBeenCalled();
 expect(m.removeFile).toHaveBeenCalledWith('books/private.pdf', expect.any(AbortSignal));
 for (const fn of [m.readings, m.permissions, m.files, m.book]) expect(fn).toHaveBeenCalledTimes(1);
 expect(m.book).toHaveBeenCalledWith({ where: { id: 8 } });
});
test('storage errors retain every database record and expose no raw error', async () => {
 m.removeFile.mockRejectedValue(new Error('secret object key'));
 const r = await app.request('/books/8', { method: 'DELETE' });
 expect(r.status).toBe(502);
 expect(await r.text()).not.toContain('secret object key');
 for (const fn of [m.readings, m.permissions, m.files, m.book]) expect(fn).not.toHaveBeenCalled();
});
test('database failure after storage deletion returns a recoverable error', async () => {
 m.book.mockRejectedValueOnce(new Error('private database detail'));
 const r = await app.request('/books/8', { method: 'DELETE' });
 expect(r.status).toBe(500);
 expect(await r.json()).toMatchObject({ code: 'PERMANENT_DELETE_FAILED' });
});
