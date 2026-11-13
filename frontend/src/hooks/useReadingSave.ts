import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import * as readingInfoApi from '@/api/readingInfo';
import type { ReadStatus, ReadingInfo } from '@/api/readingInfo';

type LatestReadingSave = {
	position: number | string;
	readStatus: Exclude<ReadStatus, 'unread'>;
	progressPercentage?: number | null;
	generation?: number;
	saveSequence?: string;
	submitted?: boolean;
};

export function useReadingSave(bookId: number, userId?: number) {
	const [status, setStatus] = useState<'unknown' | 'saved' | 'saving' | 'error'>('unknown');
	const latest = useRef<LatestReadingSave | null>(null);
	const version = useRef(0);
	const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
	const pending = useRef(false);
	const previousUserId = useRef(userId);

	const confirmInitialStatus = useCallback((info: ReadingInfo) => {
		if (version.current > 0) return;
		const hasSavedPosition = (typeof info.currentPosition === 'string' && info.currentPosition.length > 0)
			|| info.readStatus !== 'unread'
			|| info.currentPage > 1;
		setStatus(hasSavedPosition ? 'saved' : 'unknown');
	}, []);
	const activeUserId = useRef(userId);
	const sessionGeneration = readingInfoApi.getReadingSaveGeneration?.();
	const activeSessionGeneration = useRef(sessionGeneration);
	useLayoutEffect(() => {
		activeUserId.current = userId;
		activeSessionGeneration.current = sessionGeneration;
	}, [sessionGeneration, userId]);
	const isActiveSession = useCallback((originUserId?: number, originGeneration?: number) => (
		activeUserId.current === originUserId
		&& activeSessionGeneration.current === originGeneration
		&& readingInfoApi.getReadingSaveGeneration?.() === originGeneration
	), []);
	const flush = useCallback((keepalive = false) => {
		if (!isActiveSession(userId, sessionGeneration)) return;
		if (timer.current) clearTimeout(timer.current);
		if (!pending.current || !latest.current) return;
		pending.current = false;
		const attempt = version.current;
		const queued = latest.current;
		const { position, readStatus, progressPercentage, generation } = queued;
		const isCurrent = () => version.current === attempt
			&& latest.current === queued
			&& isActiveSession(userId, sessionGeneration);
		if (!isCurrent()) return;
		queued.submitted = true;
		let request: Promise<unknown>;
		try {
			request = Promise.resolve(generation === undefined
				? userId === undefined
					? readingInfoApi.saveReadingInfo(bookId, position, readStatus, progressPercentage, undefined, undefined, keepalive, queued.saveSequence)
					: readingInfoApi.saveReadingInfo(bookId, position, readStatus, progressPercentage, userId, undefined, keepalive, queued.saveSequence)
				: userId === undefined
					? readingInfoApi.saveReadingInfo(bookId, position, readStatus, progressPercentage, undefined, generation, keepalive, queued.saveSequence)
					: readingInfoApi.saveReadingInfo(bookId, position, readStatus, progressPercentage, userId, generation, keepalive, queued.saveSequence));
		} catch (error) {
			request = Promise.reject(error);
		}
		void request.then(
			() => { if (isCurrent()) setStatus('saved'); },
			(error: unknown) => {
				if (!isCurrent()) return;
				const retrySequence = error instanceof Error
					? (error as Error & { saveSequence?: unknown }).saveSequence
					: undefined;
				if (typeof retrySequence === 'string' && /^\d+$/.test(retrySequence)) {
					queued.saveSequence = retrySequence;
				}
				setStatus('error');
			},
		);
	}, [bookId, isActiveSession, sessionGeneration, userId]);
	useEffect(() => {
		if (previousUserId.current === userId) return;
		if (timer.current) clearTimeout(timer.current);
		latest.current = null;
		pending.current = false;
		++version.current;
		setStatus('unknown');
		previousUserId.current = userId;
	}, [userId]);
	useEffect(() => {
		const unregister = readingInfoApi.registerReadingFlush(bookId, flush, userId);
		const visibility = () => { if (document.hidden) flush(true); };
		const pagehide = () => flush(true);
		window.addEventListener('pagehide', pagehide);
		document.addEventListener('visibilitychange', visibility);
		return () => {
			if (isActiveSession(userId, sessionGeneration)) flush(true);
			unregister();
			window.removeEventListener('pagehide', pagehide);
			document.removeEventListener('visibilitychange', visibility);
		};
	}, [bookId, flush, isActiveSession, sessionGeneration, userId]);
	const queueSave = useCallback(
		(position: number | string, readStatus: Exclude<ReadStatus, 'unread'> = 'reading', progressPercentage?: number | null, generation = readingInfoApi.getReadingSaveGeneration?.(), existingSequence?: string) => {
			if (!isActiveSession(userId, sessionGeneration)) return;
			const sequencePromise = existingSequence === undefined && userId !== undefined && readingInfoApi.reserveReadingSave
					? readingInfoApi.reserveReadingSave(bookId, position, readStatus, progressPercentage, userId, generation)
				: existingSequence === undefined ? undefined : Promise.resolve(existingSequence);
			latest.current = { position, readStatus, progressPercentage, generation, saveSequence: existingSequence };
			const queued = latest.current;
			++version.current;
			pending.current = true;
			setStatus('saving');
			if (sequencePromise) {
				void sequencePromise.then(
					(sequence) => {
						if (latest.current === queued && !queued.submitted) queued.saveSequence = sequence;
					},
					() => undefined,
				);
			}
			if (timer.current) clearTimeout(timer.current);
			if (readStatus === 'completed') flush();
			else timer.current = setTimeout(flush, 750);
		},
		[bookId, flush, isActiveSession, sessionGeneration, userId],
	);
	const save = useCallback((position: number | string, readStatus: Exclude<ReadStatus, 'unread'> = 'reading', progressPercentage?: number | null) => {
		queueSave(position, readStatus, progressPercentage);
	}, [queueSave]);
	const retry = useCallback(() => {
		if (!isActiveSession(userId, sessionGeneration)) return;
		if (latest.current) {
			const { position, readStatus, progressPercentage, generation, saveSequence } = latest.current;
			queueSave(position, readStatus, progressPercentage, generation, saveSequence);
			flush();
		}
	}, [flush, isActiveSession, queueSave, sessionGeneration, userId]);
	return { save, status, retry, confirmInitialStatus };
}
