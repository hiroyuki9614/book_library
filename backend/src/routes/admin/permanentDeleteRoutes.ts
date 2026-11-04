import { Hono } from 'hono';
import type { PrismaVariables } from '../../lib/prisma.js';
import { auth } from '../../lib/auth.js';
import { deleteStoredBookFile } from '../../lib/bookStorage.js';

const app = new Hono<PrismaVariables>();
class StorageDeletionError extends Error {}

app.delete('/books/:bookId', async (c) => {
 try {
 const session = await auth.api.getSession({ headers: c.req.raw.headers });
 if (!session) return c.json({ code: 'UNAUTHORIZED' }, 401);
 const userId = Number(session.user.id);
 if (!Number.isSafeInteger(userId)) return c.json({ code: 'UNAUTHORIZED' }, 401);
 const prisma = c.get('prisma');
 const user = await prisma.user.findFirst({
  where: { id: userId, deletedAt: null }, select: { id: true, role: { select: { name: true } } },
 });
 if (user?.role.name !== 'admin') return c.json({ code: 'FORBIDDEN' }, 403);
 const rawId = c.req.param('bookId');
 const bookId = Number(rawId);
 if (!/^[1-9]\d*$/.test(rawId) || !Number.isSafeInteger(bookId)) return c.json({ code: 'BOOK_NOT_FOUND' }, 404);
  const result = await prisma.$transaction(async (tx) => {
   // Restore takes the same row lock before checking files.
   await tx.$queryRawUnsafe('SELECT id FROM books WHERE id = $1 FOR UPDATE', bookId);
   const book = await tx.book.findUnique({ where: { id: bookId }, include: { bookFiles: true } });
   if (!book) return 'missing' as const;
   if (!book.deletedAt) return 'active' as const;
   try {
    const signal = AbortSignal.timeout(10_000);
    const outcomes = await Promise.allSettled(book.bookFiles.map((file) => deleteStoredBookFile(file.fileUrl, signal)));
    if (outcomes.some((outcome) => outcome.status === 'rejected')) throw new StorageDeletionError();
   } catch {
    throw new StorageDeletionError();
   }
   await tx.readingInfo.deleteMany({ where: { bookId } });
   await tx.roleBookPermission.deleteMany({ where: { bookId } });
   await tx.bookFile.deleteMany({ where: { bookId } });
   await tx.book.delete({ where: { id: bookId } });
   return 'deleted' as const;
  }, { timeout: 30_000, maxWait: 5_000 });
  if (result === 'missing') return c.json({ code: 'BOOK_NOT_FOUND' }, 404);
  if (result === 'active') return c.json({ code: 'BOOK_NOT_DELETED', message: '先に通常の削除を実行してください。' }, 409);
  return c.json({ id: bookId, permanentlyDeleted: true });
 } catch (error) {
  // A retry is idempotent. The deleted list probes for missing files after DB rollback.
  if (error instanceof StorageDeletionError) {
   return c.json({ code: 'FILE_DELETE_FAILED', message: 'ファイル削除に失敗しました。書籍情報を保持しています。再実行してください。' }, 502);
  }
  return c.json({ code: 'PERMANENT_DELETE_FAILED', message: '完全削除を完了できませんでした。削除済み一覧から再実行してください。' }, 500);
 }
});
export default app;
