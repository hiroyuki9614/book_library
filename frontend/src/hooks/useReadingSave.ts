import { useCallback, useEffect, useRef, useState } from 'react';
import { registerReadingFlush, saveReadingInfo, type ReadStatus } from '@/api/readingInfo';

export function useReadingSave(bookId: number, userId?: number) {
	const [status, setStatus] = useState<'saved' | 'saving' | 'error'>('saved');
	const latest = useRef<{ position: number | string; readStatus: Exclude<ReadStatus, 'unread'>; progressPercentage?: number | null } | null>(null);
	const version = useRef(0);
	const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
	const pending = useRef(false);
	const previousUserId = useRef(userId);
	const flush = useCallback(() => {
		if (timer.current) clearTimeout(timer.current);
		if (!pending.current || !latest.current) return;
		pending.current = false;
		const attempt = version.current;
		const { position, readStatus, progressPercentage } = latest.current;
		const request = userId === undefined
			? saveReadingInfo(bookId, position, readStatus, progressPercentage)
			: saveReadingInfo(bookId, position, readStatus, progressPercentage, userId);
		void request.then(
			() => { if (version.current === attempt) setStatus('saved'); },
			() => { if (version.current === attempt) setStatus('error'); },
		);
	}, [bookId, userId]);
	useEffect(() => {
		if (previousUserId.current === userId) return;
		if (timer.current) clearTimeout(timer.current);
		latest.current = null;
		pending.current = false;
		++version.current;
		setStatus('saved');
		previousUserId.current = userId;
	}, [userId]);
	useEffect(() => {
		const unregister = registerReadingFlush(bookId, flush, userId);
		const visibility = () => { if (document.hidden) flush(); };
		window.addEventListener('pagehide', flush);
		document.addEventListener('visibilitychange', visibility);
		return () => {
			flush(); unregister();
			window.removeEventListener('pagehide', flush);
			document.removeEventListener('visibilitychange', visibility);
		};
	}, [bookId, flush, userId]);
	const save = useCallback(
		(position: number | string, readStatus: Exclude<ReadStatus, 'unread'> = 'reading', progressPercentage?: number | null) => {
			latest.current = { position, readStatus, progressPercentage };
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
		if (latest.current) { save(latest.current.position, latest.current.readStatus, latest.current.progressPercentage); flush(); }
	};
	return { save, status, retry };
}
