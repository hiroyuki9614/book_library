import { useCallback, useEffect, useRef, useState } from 'react';
import { registerReadingFlush, saveReadingInfo, type ReadStatus } from '@/api/readingInfo';

export function useReadingSave(bookId: number) {
	const [status, setStatus] = useState<'saved' | 'saving' | 'error'>('saved');
	const latest = useRef<{ position: number | string; readStatus: Exclude<ReadStatus, 'unread'> } | null>(null);
	const version = useRef(0);
	const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
	const pending = useRef(false);
	const flush = useCallback(() => {
		if (timer.current) clearTimeout(timer.current);
		if (!pending.current || !latest.current) return;
		pending.current = false;
		const attempt = version.current;
		const { position, readStatus } = latest.current;
		void saveReadingInfo(bookId, position, readStatus).then(
			() => { if (version.current === attempt) setStatus('saved'); },
			() => { if (version.current === attempt) setStatus('error'); },
		);
	}, [bookId]);
	useEffect(() => {
		const unregister = registerReadingFlush(bookId, flush);
		const visibility = () => { if (document.hidden) flush(); };
		window.addEventListener('pagehide', flush);
		document.addEventListener('visibilitychange', visibility);
		return () => {
			flush(); unregister();
			window.removeEventListener('pagehide', flush);
			document.removeEventListener('visibilitychange', visibility);
		};
	}, [bookId, flush]);
	const save = useCallback(
		(position: number | string, readStatus: Exclude<ReadStatus, 'unread'> = 'reading') => {
			latest.current = { position, readStatus };
			++version.current;
			pending.current = true;
			setStatus('saving');
			if (timer.current) clearTimeout(timer.current);
			if (readStatus === 'completed') flush();
			else timer.current = setTimeout(flush, 750);
		},
		[flush],
	);
	const retry = () => {
		if (latest.current) { save(latest.current.position, latest.current.readStatus); flush(); }
	};
	return { save, status, retry };
}
