import Dexie from 'dexie';
import type { EntityTable } from 'dexie';
import type { File } from 'zod/v4/core';

type Book = {
	id: number;
	title: string;
	fileType: 'epub' | 'pdf';
	file: Blob;
	progress: number;
	createdAt: Date;
};

const db = new Dexie('BooksDatabase') as Dexie & {
	books: EntityTable<Book, 'id'>;
};

db.version(1).stores({
	books: '++id, title, fileType, progress, createdAt',
});

export type { Book };
export { db };
