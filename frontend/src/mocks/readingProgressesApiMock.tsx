import { readingProgressData } from '@/data/readingProgress';
import type { ReadingProgress } from '@/data/readingProgress';

async function readingProgressesApiMock(timeout = 1000): Promise<ReadingProgress[]> {
	try {
		const result = await new Promise<ReadingProgress[]>((resolve) => {
			setTimeout(() => {
				resolve(readingProgressData);
			}, timeout);
		});
		return result;
	} catch (error) {
		console.error('書籍一覧の取得に失敗:', error);
		throw error;
	}
}

export default readingProgressesApiMock;
