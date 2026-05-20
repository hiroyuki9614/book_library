import { booksData } from '@/data/booksData';
import type { Book } from '@/data/booksData';

async function booksApiMock(timeout = 1000): Promise<Book[]> {
	try {
		const result = await new Promise<Book[]>((resolve) => {
			setTimeout(() => {
				resolve(booksData);
			}, timeout);
		});
		// jsonで返す
		return result;
	} catch (error) {
		console.error('書籍一覧の取得に失敗:', error);
		throw error;
	}
}

export default booksApiMock;
