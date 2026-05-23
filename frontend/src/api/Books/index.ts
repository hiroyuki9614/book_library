import { db } from '@/features/funcs/db';
import { useLiveQuery } from 'dexie-react-hooks';
import type { Book } from '@/data/booksData';
import type { ReadingProgress } from '@/data/readingProgress';

type BooksProps = {
	books: [...Book[], file: Blob];
	readingProgresses: ReadingProgress[];
};

export default function Books({ books, readingProgresses }: BooksProps) {
	const books = useLiveQuery(() => db.books.toArray(), []);

	// 書籍API
	const addBook = async (book: Book) => {
		db.books.add({
			title: book.title,
			fileType: book.fileType,
			file: book.file,
			createdAt: book.createdAt,
		});
	};

	const updateBook = (id: number, updatedFields: Partial<Book>) => {
		db.books.update(id, updatedFields);
	};

	const deleteBook = (id: number) => {
		db.books.delete(id);
	};

	const getBook = (id: number) => {
		return db.books.get(id);
	};
	// 進捗API
	const addReadingProgress = (bookId: number, progress: number) => {
		db.readingProgresses.add({
			bookId,
			progress,
			updatedAt: new Date(),
		});
	};

	const updateReadingProgress = (id: number, progress: number) => {
		db.readingProgresses.update(id, { progress, updatedAt: new Date() });
	};

	const deleteReadingProgress = (id: number) => {
		db.readingProgresses.delete(id);
	};

	const getReadingProgress = (bookId: number) => {
		return db.readingProgresses.where('bookId').equals(bookId).toArray();
	};

	return null;
}
