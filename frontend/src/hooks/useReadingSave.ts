import { useCallback, useRef, useState } from 'react';
import { saveReadingInfo, type ReadStatus } from '@/api/readingInfo';

export function useReadingSave(bookId: number) {
	const [status, setStatus] = useState<'saved' | 'saving' | 'error'>('saved');
	const latest = useRef<{ position: number | string; readStatus: Exclude<ReadStatus, 'unread'> } | null>(null);
	const version = useRef(0);
	const save = useCallback(
		(position: number | string, readStatus: Exclude<ReadStatus, 'unread'> = 'reading') => {
			latest.current = { position, readStatus };
			const attempt = ++version.current;
			setStatus('saving');
			void saveReadingInfo(bookId, position, readStatus).then(
				() => {
					if (version.current === attempt) setStatus('saved');
				},
				() => {
					if (version.current === attempt) setStatus('error');
				},
			);
		},
		[bookId],
	);
	const retry = () => {
		if (latest.current) save(latest.current.position, latest.current.readStatus);
	};
	return { save, status, retry };
}
