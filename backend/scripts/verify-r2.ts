import { timingSafeEqual } from 'node:crypto';
import {
	deleteStoredBookFile,
	getBookFileAccess,
	getBookStorageDriver,
	storeBookFile,
} from '../src/lib/bookStorage.js';

if (getBookStorageDriver() !== 'r2') {
	throw new Error('BOOK_FILE_STORAGE_DRIVER=r2 is required for the R2 smoke check');
}

const expected = Buffer.from('%PDF-1.7\n% BeLib R2 smoke check\n');
let storedFileUrl: string | undefined;

try {
	const stored = await storeBookFile({
		fileType: 'pdf',
		bytes: expected,
		mimeType: 'application/pdf',
		originalFileName: 'belib-r2-smoke.pdf',
	});
	storedFileUrl = stored.fileUrl;

	const access = await getBookFileAccess(stored.fileUrl);
	if (access.kind !== 'signed-url') {
		throw new Error('R2 storage did not issue a signed URL');
	}

	const response = await fetch(access.url);
	if (!response.ok) {
		throw new Error(`Signed R2 GET failed with HTTP ${response.status}`);
	}
	const actual = Buffer.from(await response.arrayBuffer());
	if (actual.length !== expected.length || !timingSafeEqual(actual, expected)) {
		throw new Error('R2 smoke object content did not match the uploaded bytes');
	}

	console.log(`R2_SMOKE_OK key=${stored.fileUrl} status=${response.status} expiresAt=${access.expiresAt}`);
} finally {
	if (storedFileUrl) {
		await deleteStoredBookFile(storedFileUrl);
		console.log(`R2_SMOKE_CLEANUP_OK key=${storedFileUrl}`);
	}
}
