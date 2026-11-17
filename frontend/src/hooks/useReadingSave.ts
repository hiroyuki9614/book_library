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
	reservation?: Promise<string | undefined>;
	flushPromise?: Promise<boolean>;
	flushResult?: boolean;
	keepalive?: boolean;
};

export function useReadingSave(bookId: number, userId?: number) {
	const [status, setStatus] = useState<'unknown' | 'saved' | 'saving' | 'error' | 'conflict' | 'stale'>('unknown');
	const latest = useRef<LatestReadingSave | null>(null);
	const version = useRef(0);
	const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
	const pending = useRef(false);
	const previousUserId = useRef(userId);

	const confirmInitialStatus = useCallback((info: ReadingInfo) => {
		if (version.current > 0) return;
		if (info.saveConflict) { setStatus('conflict'); return; }
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
	const flush = useCallback((keepalive = false): Promise<boolean> | undefined => {
		if (!isActiveSession(userId, sessionGeneration)) return Promise.resolve(false);
		if (timer.current) clearTimeout(timer.current);
		const queued = latest.current;
		if (!queued) return undefined;
		if (keepalive) queued.keepalive = true;
		if (queued.flushPromise) return queued.flushPromise;
		if (!pending.current) return undefined;
		const attempt = version.current;
		const { position, readStatus, progressPercentage, generation } = queued;
		const isCurrent = () => version.current === attempt
			&& latest.current === queued
			&& isActiveSession(userId, sessionGeneration);
		if (!isCurrent()) return Promise.resolve(false);
		const run = async () => {
			let sequence = queued.saveSequence;
			if (userId !== undefined && (sequence === undefined || !/^\d+$/.test(sequence))) {
				try {
					sequence = await queued.reservation;
				} catch {
					sequence = undefined;
				}
				if (!isCurrent()) return false;
				if (sequence === undefined || !/^\d+$/.test(sequence)) {
					setStatus('error');
					return false;
				}
				queued.saveSequence = sequence;
			}
			if (!isCurrent()) return false;
			queued.submitted = true;
			pending.current = false;
			try {
				await (generation === undefined
					? userId === undefined
						? readingInfoApi.saveReadingInfo(bookId, position, readStatus, progressPercentage, undefined, undefined, queued.keepalive ?? false, sequence)
						: readingInfoApi.saveReadingInfo(bookId, position, readStatus, progressPercentage, userId, undefined, queued.keepalive ?? false, sequence)
					: userId === undefined
						? readingInfoApi.saveReadingInfo(bookId, position, readStatus, progressPercentage, undefined, generation, queued.keepalive ?? false, sequence)
						: readingInfoApi.saveReadingInfo(bookId, position, readStatus, progressPercentage, userId, generation, queued.keepalive ?? false, sequence));
				if (isCurrent()) setStatus('saved');
				else return false;
				return true;
			} catch (error) {
				if (!isCurrent()) return false;
				const errorCode = error instanceof Error ? (error as Error & { code?: string }).code : undefined;
				if (errorCode === 'STALE_READING_SAVE') {
					latest.current = null;
					pending.current = false;
					setStatus('stale');
					return false;
				}
				if (errorCode === 'READING_SESSION_MISMATCH') {
					latest.current = null;
					pending.current = false;
					setStatus('conflict');
					return false;
				}
				const retrySequence = error instanceof Error
					? (error as Error & { saveSequence?: unknown }).saveSequence
					: undefined;
				if (typeof retrySequence === 'string' && /^\d+$/.test(retrySequence)) queued.saveSequence = retrySequence;
				setStatus('error');
				return false;
			}
		};
		const submitted = run().then((saved) => {
			queued.flushResult = saved;
			return saved;
		});
		queued.flushPromise = submitted;
		void submitted.finally(() => {
			if (queued.flushPromise === submitted) queued.flushPromise = undefined;
		});
		return submitted;
	}, [bookId, isActiveSession, sessionGeneration, userId]);
	const flushRegistered = useCallback(async (): Promise<boolean> => {
		if (!isActiveSession(userId, sessionGeneration)) return false;
		let queued = latest.current;
		if (!queued) return true;
		while (queued) {
			if (queued.flushPromise) {
				const saved = await queued.flushPromise;
				if (!isActiveSession(userId, sessionGeneration)) return false;
				if (latest.current === queued) return saved;
			} else if (queued.submitted) {
				if (latest.current === queued) return queued.flushResult ?? false;
			} else if (latest.current === queued) {
				const request = flush();
				if (!request) return !pending.current;
				const saved = await request;
				if (!isActiveSession(userId, sessionGeneration)) return false;
				if (latest.current === queued) return saved;
			}
			queued = latest.current;
		}
		return false;
	}, [flush, isActiveSession, sessionGeneration, userId]);
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
		const unregister = readingInfoApi.registerReadingFlush(bookId, flushRegistered, userId);
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
	}, [bookId, flush, flushRegistered, isActiveSession, sessionGeneration, userId]);
	const queueSave = useCallback(
		(position: number | string, readStatus: Exclude<ReadStatus, 'unread'> = 'reading', progressPercentage?: number | null, generation = readingInfoApi.getReadingSaveGeneration?.(), existingSequence?: string) => {
			if (!isActiveSession(userId, sessionGeneration)) return;
			const previousIntent = latest.current;
			const inheritKeepalive = previousIntent?.keepalive === true
				&& previousIntent.submitted !== true
				&& previousIntent.flushPromise !== undefined;
			const sequencePromise = existingSequence === undefined && userId !== undefined && readingInfoApi.reserveReadingSave
					? readingInfoApi.reserveReadingSave(bookId, position, readStatus, progressPercentage, userId, generation)
				: existingSequence === undefined ? undefined : Promise.resolve(existingSequence);
			latest.current = {
				position,
				readStatus,
				progressPercentage,
				generation,
				saveSequence: existingSequence,
				...(inheritKeepalive ? { keepalive: true } : {}),
			};
			const queued = latest.current;
			++version.current;
			pending.current = true;
			setStatus('saving');
			if (sequencePromise) {
				queued.reservation = sequencePromise;
				void sequencePromise.then(
					(sequence) => {
						if (latest.current === queued && !queued.submitted && typeof sequence === 'string' && /^\d+$/.test(sequence)) queued.saveSequence = sequence;
					},
					() => { if (latest.current === queued && !queued.submitted) queued.saveSequence = undefined; },
				);
			}
			if (timer.current) clearTimeout(timer.current);
			if (readStatus === 'completed' || inheritKeepalive) flush(inheritKeepalive);
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
