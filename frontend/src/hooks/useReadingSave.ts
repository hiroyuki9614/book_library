import { useCallback, useEffect, useRef, useState } from 'react';
import * as readingInfoApi from '@/api/readingInfo';
import type { ReadStatus } from '@/api/readingInfo';

type LatestReadingSave = {
	position: number | string;
	readStatus: Exclude<ReadStatus, 'unread'>;
	progressPercentage?: number | null;
	generation?: number;
};

export function useReadingSave(bookId: number, userId?: number) {
	const [status, setStatus] = useState<'saved' | 'saving' | 'error'>('saved');
	const latest = useRef<LatestReadingSave | null>(null);
	const version = useRef(0);
	const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
	const pending = useRef(false);
	const previousUserId = useRef(userId);
	const flush = useCallback(() => {
		if (timer.current) clearTimeout(timer.current);
		if (!pending.current || !latest.current) return;
		pending.current = false;
		const attempt = version.current;
		const { position, readStatus, progressPercentage, generation } = latest.current;
		const request = generation === undefined
			? userId === undefined
				? readingInfoApi.saveReadingInfo(bookId, position, readStatus, progressPercentage)
				: readingInfoApi.saveReadingInfo(bookId, position, readStatus, progressPercentage, userId)
			: userId === undefined
				? readingInfoApi.saveReadingInfo(bookId, position, readStatus, progressPercentage, undefined, generation)
				: readingInfoApi.saveReadingInfo(bookId, position, readStatus, progressPercentage, userId, generation);
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
		const unregister = readingInfoApi.registerReadingFlush(bookId, flush, userId);
		const visibility = () => { if (document.hidden) flush(); };
		window.addEventListener('pagehide', flush);
		document.addEventListener('visibilitychange', visibility);
		return () => {
			flush(); unregister();
			window.removeEventListener('pagehide', flush);
			document.removeEventListener('visibilitychange', visibility);
		};
	}, [bookId, flush, userId]);
	const queueSave = useCallback(
		(position: number | string, readStatus: Exclude<ReadStatus, 'unread'> = 'reading', progressPercentage?: number | null, generation = readingInfoApi.getReadingSaveGeneration?.()) => {
			latest.current = { position, readStatus, progressPercentage, generation };
			++version.current;
			pending.current = true;
			setStatus('saving');
			if (timer.current) clearTimeout(timer.current);
			if (readStatus === 'completed') flush();
			else timer.current = setTimeout(flush, 750);
		},
		[flush],
	);
	const save = useCallback((position: number | string, readStatus: Exclude<ReadStatus, 'unread'> = 'reading', progressPercentage?: number | null) => {
		queueSave(position, readStatus, progressPercentage);
	}, [queueSave]);
	const retry = useCallback(() => {
		if (latest.current) {
			const { position, readStatus, progressPercentage, generation } = latest.current;
			queueSave(position, readStatus, progressPercentage, generation);
			flush();
		}
	}, [flush, queueSave]);
	return { save, status, retry };
}
