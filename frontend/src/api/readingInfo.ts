import { apiFetch, getApiBaseUrl } from '@/lib/api-client';

export type ReadStatus = 'unread' | 'reading' | 'completed';

export type ReadingInfo = {
	bookId: number;
	currentPosition: string | null;
	currentPage: number;
	progressPercentage: number | null;
	readStatus: ReadStatus;
	saveSequence?: string;
	saveConflict?: boolean;
};

const READING_SESSION_USER_HEADER = 'X-BeLib-User-Id';
const READING_SAVE_DB_NAME = 'belib-reading-saves';
const READING_SAVE_DB_VERSION = 2;
const READING_SAVE_SEQUENCE_STORE = 'sequence';
const READING_SAVE_SCOPED_SEQUENCE_STORE = 'sequence-by-scope';
const READING_SAVE_OUTBOX_STORE = 'outbox';
const READING_SAVE_SEQUENCE_VERSION = 3;
const KEEPALIVE_BODY_LIMIT_BYTES = 64 * 1024;
const MAX_READING_SAVE_SEQUENCE = 9_223_372_036_854_775_807n;

let readingSaveGeneration = 0;
const inFlightReadingSaves = new Set<AbortController>();

type ReadingSavePayload = {
	position: number | string;
	readStatus: Exclude<ReadStatus, 'unread'>;
	progressPercentage?: number | null;
};

type PendingReadingSave = ReadingSavePayload & {
	scopeKey: string;
	bookId: number;
	userId: number;
	saveSequence: string;
	sequenceVersion?: number;
	expectedSaveSequence?: string;
	hasProgressPercentage: boolean;
	generation?: number;
};

type ScopedReadingSaveSequence = {
	scopeKey: string;
	saveSequence: string;
	serverSequence?: string;
};

let readingSaveDbPromise: Promise<IDBDatabase> | undefined;
const reservedReadingSaveBases = new Map<string, string>();

function readingSaveReservationKey(scopeKey: string, sequence: string) {
	return `${scopeKey}\u0000${sequence}`;
}

type ReadingSaveSequence = string | number;

function normalizeReadingSaveSequence(value: unknown) {
	if (typeof value === 'number') {
		return Number.isSafeInteger(value) && value >= 0 ? String(value) : undefined;
	}
	if (typeof value !== 'string' || !/^\d+$/.test(value)) return undefined;
	try {
		const sequence = BigInt(value);
		return sequence <= MAX_READING_SAVE_SEQUENCE ? sequence.toString() : undefined;
	} catch {
		return undefined;
	}
}

function compareReadingSaveSequences(left: unknown, right: unknown) {
	const normalizedLeft = normalizeReadingSaveSequence(left);
	const normalizedRight = normalizeReadingSaveSequence(right);
	if (normalizedLeft === undefined || normalizedRight === undefined) return null;
	const leftBigInt = BigInt(normalizedLeft);
	const rightBigInt = BigInt(normalizedRight);
	return leftBigInt === rightBigInt ? 0 : leftBigInt < rightBigInt ? -1 : 1;
}

function nextReadingSaveSequence(serverSequence: string) {
	const server = BigInt(serverSequence);
	const next = server + 1n;
	if (next > MAX_READING_SAVE_SEQUENCE) {
		throw new Error('Reading save sequence is exhausted');
	}
	return next.toString();
}

function assertReadingSaveGeneration(generation: number) {
	if (generation !== readingSaveGeneration) {
		throw new Error('Reading save invalidated by authentication change');
	}
}

function getReadingUserId(userId: number | undefined) {
	return userId !== undefined && Number.isSafeInteger(userId) && userId > 0 ? userId : null;
}

function getReadingScopeKey(bookId: number, userId?: number) {
	return `${userId === undefined ? 'anonymous' : `user:${userId}`}:book:${bookId}`;
}

function openReadingSaveDatabase() {
	if (typeof indexedDB === 'undefined') {
		return Promise.reject(new Error('IndexedDB is required for durable reading saves'));
	}
	if (readingSaveDbPromise) return readingSaveDbPromise;

	const opening = new Promise<IDBDatabase>((resolve, reject) => {
		const request = indexedDB.open(READING_SAVE_DB_NAME, READING_SAVE_DB_VERSION);
		request.onupgradeneeded = () => {
			const database = request.result;
			if (!database.objectStoreNames.contains(READING_SAVE_SEQUENCE_STORE)) {
				database.createObjectStore(READING_SAVE_SEQUENCE_STORE, { autoIncrement: true });
			}
			if (!database.objectStoreNames.contains(READING_SAVE_SCOPED_SEQUENCE_STORE)) {
				database.createObjectStore(READING_SAVE_SCOPED_SEQUENCE_STORE, { keyPath: 'scopeKey' });
			}
			if (!database.objectStoreNames.contains(READING_SAVE_OUTBOX_STORE)) {
				database.createObjectStore(READING_SAVE_OUTBOX_STORE, { keyPath: 'scopeKey' });
			}
		};
		request.onsuccess = () => resolve(request.result);
		request.onerror = () => reject(request.error ?? new Error('Could not open reading save storage'));
	});
	readingSaveDbPromise = opening;
	return opening.catch((error: unknown) => {
		if (readingSaveDbPromise === opening) readingSaveDbPromise = undefined;
		throw error;
	});
}

function reserveReadingSaveSequence(payload: ReadingSavePayload, bookId: number, userId: number, generation: number) {
	const scopeKey = getReadingScopeKey(bookId, userId);
	return openReadingSaveDatabase().then((database) => new Promise<string>((resolve, reject) => {
		if (generation !== readingSaveGeneration) {
			reject(new Error('Reading save invalidated by authentication change'));
			return;
		}
		const transaction = database.transaction([READING_SAVE_SCOPED_SEQUENCE_STORE, READING_SAVE_OUTBOX_STORE], 'readwrite');
		const sequenceStore = transaction.objectStore(READING_SAVE_SCOPED_SEQUENCE_STORE);
		const outboxStore = transaction.objectStore(READING_SAVE_OUTBOX_STORE);
		let sequence: string | undefined;
		let settled = false;
		let storedSequence: ScopedReadingSaveSequence | undefined;
		let pendingSave: PendingReadingSave | undefined;
		let sequenceLoaded = false;
		let pendingLoaded = false;
		const finishWithError = (error: unknown) => {
			if (!settled) {
				settled = true;
				reject(error instanceof Error ? error : new Error('Could not reserve reading save sequence'));
			}
		};
		const reserve = () => {
			if (settled || !sequenceLoaded || !pendingLoaded) return;
			if (generation !== readingSaveGeneration) {
				finishWithError(new Error('Reading save invalidated by authentication change'));
				transaction.abort();
				return;
			}
			try {
				const server = storedSequence?.serverSequence === undefined ? undefined : normalizeReadingSaveSequence(storedSequence.serverSequence);
				if (server === undefined) throw new Error('Reading save baseline must be synchronized from the server before reserving');
				const pending = pendingSave?.sequenceVersion === READING_SAVE_SEQUENCE_VERSION
					? normalizeReadingSaveSequence(pendingSave.saveSequence)
					: undefined;
				if (pendingSave?.sequenceVersion === READING_SAVE_SEQUENCE_VERSION && pending === undefined) {
					throw new Error('Pending reading save sequence is invalid');
				}
				const previous = pending !== undefined && BigInt(pending) > BigInt(server) ? pending : server;
				sequence = nextReadingSaveSequence(previous);
				sequenceStore.put({ scopeKey, saveSequence: sequence, serverSequence: server } satisfies ScopedReadingSaveSequence);
				outboxStore.put({
					...payload,
					scopeKey,
					bookId,
					userId,
					saveSequence: sequence,
					sequenceVersion: READING_SAVE_SEQUENCE_VERSION,
					expectedSaveSequence: server,
					hasProgressPercentage: payload.progressPercentage !== undefined,
					generation,
				});
			} catch (error) {
				finishWithError(error);
				transaction.abort();
			}
		};
		const sequenceRequest = sequenceStore.get(scopeKey);
		sequenceRequest.onsuccess = () => {
			storedSequence = sequenceRequest.result as ScopedReadingSaveSequence | undefined;
			sequenceLoaded = true;
			reserve();
		};
		sequenceRequest.onerror = () => finishWithError(sequenceRequest.error);
		const pendingRequest = outboxStore.get(scopeKey);
		pendingRequest.onsuccess = () => {
			pendingSave = pendingRequest.result as PendingReadingSave | undefined;
			pendingLoaded = true;
			reserve();
		};
		pendingRequest.onerror = () => finishWithError(pendingRequest.error);
		transaction.oncomplete = () => {
			if (!settled && sequence !== undefined) {
				settled = true;
				const stored = storedSequence?.serverSequence;
				if (stored !== undefined) reservedReadingSaveBases.set(readingSaveReservationKey(scopeKey, sequence), stored);
				resolve(sequence);
			}
		};
		transaction.onerror = () => finishWithError(transaction.error);
		transaction.onabort = () => finishWithError(transaction.error ?? new Error('Reading save storage transaction aborted'));
	}));
}

function readPendingReadingSave(scopeKey: string) {
	return openReadingSaveDatabase().then((database) => new Promise<PendingReadingSave | null>((resolve, reject) => {
		const transaction = database.transaction(READING_SAVE_OUTBOX_STORE, 'readonly');
		const request = transaction.objectStore(READING_SAVE_OUTBOX_STORE).get(scopeKey);
		request.onsuccess = () => resolve((request.result as PendingReadingSave | undefined) ?? null);
		request.onerror = () => reject(request.error ?? new Error('Could not read pending reading save'));
	}));
}

function clearPendingReadingSave(scopeKey: string, saveSequence: ReadingSaveSequence) {
	const normalizedSequence = normalizeReadingSaveSequence(saveSequence);
	if (normalizedSequence === undefined) return Promise.reject(new Error('Invalid reading save sequence'));
	return openReadingSaveDatabase().then((database) => new Promise<void>((resolve, reject) => {
		const transaction = database.transaction(READING_SAVE_OUTBOX_STORE, 'readwrite');
		const store = transaction.objectStore(READING_SAVE_OUTBOX_STORE);
		const request = store.get(scopeKey);
		request.onsuccess = () => {
			const current = request.result as PendingReadingSave | undefined;
			if (current && compareReadingSaveSequences(current.saveSequence, normalizedSequence) !== 1) {
				store.delete(scopeKey);
				reservedReadingSaveBases.delete(readingSaveReservationKey(scopeKey, current.saveSequence));
			}
		};
		request.onerror = () => reject(request.error ?? new Error('Could not read pending reading save'));
		transaction.oncomplete = () => resolve();
		transaction.onerror = () => reject(transaction.error ?? new Error('Could not clear pending reading save'));
		transaction.onabort = () => reject(transaction.error ?? new Error('Could not clear pending reading save'));
	}));
}

function readScopedReadingSaveSequence(scopeKey: string) {
	return openReadingSaveDatabase().then((database) => new Promise<ScopedReadingSaveSequence | undefined>((resolve, reject) => {
		const transaction = database.transaction(READING_SAVE_SCOPED_SEQUENCE_STORE, 'readonly');
		const request = transaction.objectStore(READING_SAVE_SCOPED_SEQUENCE_STORE).get(scopeKey);
		request.onsuccess = () => resolve(request.result as ScopedReadingSaveSequence | undefined);
		request.onerror = () => reject(request.error ?? new Error('Could not read reading save baseline'));
	}));
}

function observeServerReadingSaveSequence(scopeKey: string, serverSequence: string, generation: number) {
	return openReadingSaveDatabase().then((database) => new Promise<void>((resolve, reject) => {
		if (generation !== readingSaveGeneration) {
			reject(new Error('Reading save invalidated by authentication change'));
			return;
		}
		const transaction = database.transaction(READING_SAVE_SCOPED_SEQUENCE_STORE, 'readwrite');
		const store = transaction.objectStore(READING_SAVE_SCOPED_SEQUENCE_STORE);
		const request = store.get(scopeKey);
		request.onsuccess = () => {
			if (generation !== readingSaveGeneration) { transaction.abort(); return; }
			const current = request.result as ScopedReadingSaveSequence | undefined;
			const currentServer = current?.serverSequence === undefined ? '0' : normalizeReadingSaveSequence(current.serverSequence);
			const currentLocal = current === undefined ? '0' : normalizeReadingSaveSequence(current.saveSequence);
			if (currentServer === undefined || currentLocal === undefined) { transaction.abort(); return; }
			const observed = BigInt(serverSequence) > BigInt(currentServer) ? serverSequence : currentServer;
			const floor = BigInt(currentLocal) > BigInt(observed) ? currentLocal : observed;
			store.put({ scopeKey, saveSequence: floor, serverSequence: observed } satisfies ScopedReadingSaveSequence);
		};
		request.onerror = () => reject(request.error ?? new Error('Could not read reading save baseline'));
		transaction.oncomplete = () => resolve();
		transaction.onerror = () => reject(transaction.error ?? new Error('Could not update reading save baseline'));
		transaction.onabort = () => reject(transaction.error ?? new Error('Reading save baseline update aborted'));
	}));
}

function acknowledgeReadingSave(scopeKey: string, saveSequence: string, expectedSaveSequence: string, generation: number) {
	return openReadingSaveDatabase().then((database) => new Promise<void>((resolve, reject) => {
		if (generation !== readingSaveGeneration) { reject(new Error('Reading save invalidated by authentication change')); return; }
		const transaction = database.transaction([READING_SAVE_SCOPED_SEQUENCE_STORE, READING_SAVE_OUTBOX_STORE], 'readwrite');
		const sequenceStore = transaction.objectStore(READING_SAVE_SCOPED_SEQUENCE_STORE);
		const outboxStore = transaction.objectStore(READING_SAVE_OUTBOX_STORE);
		let sequenceLoaded = false; let outboxLoaded = false;
		let sequenceRecord: ScopedReadingSaveSequence | undefined; let pending: PendingReadingSave | undefined;
		const apply = () => {
			if (!sequenceLoaded || !outboxLoaded || generation !== readingSaveGeneration) return;
			const server = sequenceRecord?.serverSequence === undefined ? '0' : normalizeReadingSaveSequence(sequenceRecord.serverSequence);
			const local = sequenceRecord === undefined ? '0' : normalizeReadingSaveSequence(sequenceRecord.saveSequence);
			if (server === undefined || local === undefined) { transaction.abort(); return; }
			const newServer = BigInt(server) > BigInt(saveSequence) ? server : saveSequence;
			const newLocal = BigInt(local) > BigInt(newServer) ? local : newServer;
			sequenceStore.put({ scopeKey, saveSequence: newLocal, serverSequence: newServer } satisfies ScopedReadingSaveSequence);
			if (!pending) return;
			const pendingSequence = normalizeReadingSaveSequence(pending.saveSequence);
			if (pendingSequence === undefined) { transaction.abort(); return; }
			if (pendingSequence === saveSequence) outboxStore.delete(scopeKey);
			else if (BigInt(pendingSequence) > BigInt(saveSequence)
				&& pending.sequenceVersion === READING_SAVE_SEQUENCE_VERSION
				&& pending.expectedSaveSequence === expectedSaveSequence) {
				outboxStore.put({ ...pending, expectedSaveSequence: saveSequence });
				reservedReadingSaveBases.set(readingSaveReservationKey(scopeKey, pendingSequence), saveSequence);
			}
		};
		const sequenceRequest = sequenceStore.get(scopeKey);
		sequenceRequest.onsuccess = () => { sequenceRecord = sequenceRequest.result as ScopedReadingSaveSequence | undefined; sequenceLoaded = true; apply(); };
		sequenceRequest.onerror = () => reject(sequenceRequest.error ?? new Error('Could not read reading save sequence'));
		const pendingRequest = outboxStore.get(scopeKey);
		pendingRequest.onsuccess = () => { pending = pendingRequest.result as PendingReadingSave | undefined; outboxLoaded = true; apply(); };
		pendingRequest.onerror = () => reject(pendingRequest.error ?? new Error('Could not read pending reading save'));
		transaction.oncomplete = () => {
			reservedReadingSaveBases.delete(readingSaveReservationKey(scopeKey, saveSequence));
			resolve();
		};
		transaction.onerror = () => reject(transaction.error ?? new Error('Could not acknowledge reading save'));
		transaction.onabort = () => reject(transaction.error ?? new Error('Reading save acknowledgement aborted'));
	}));
}

async function getExpectedReadingSaveSequence(scopeKey: string, sequence: string) {
	const reserved = reservedReadingSaveBases.get(readingSaveReservationKey(scopeKey, sequence));
	if (reserved !== undefined) return reserved;
	const pending = await readPendingReadingSave(scopeKey);
	if (!pending || compareReadingSaveSequences(pending.saveSequence, sequence) !== 0) {
		throw new Error('Reading save was superseded by a newer pending save');
	}
	const expected = normalizeReadingSaveSequence(pending.expectedSaveSequence);
	if (pending.sequenceVersion !== READING_SAVE_SEQUENCE_VERSION || expected === undefined) {
		throw new Error('Reading save has no verified server baseline');
	}
	return expected;
}

type ReadingApiError = Error & { status?: number; code?: string; saveSequence?: string; authoritativeReadingInfo?: ReadingInfo };

async function patchReadingInfo(path: string, options: RequestInit) {
	const baseUrl = getApiBaseUrl();
	if (!baseUrl) throw new Error('VITE_API_BASE_URL is not configured');

	const response = await fetch(`${baseUrl}${path}`, {
		...options,
		credentials: 'include',
		headers: { ...options.headers },
	});
	if (!response.ok) {
		let responseBody: unknown;
		try {
			responseBody = await response.json();
		} catch {
			responseBody = undefined;
		}
		const error = new Error(`API request failed: ${response.status}`) as ReadingApiError;
		error.status = response.status;
		if (typeof responseBody === 'object' && responseBody !== null && 'code' in responseBody && typeof responseBody.code === 'string') {
			error.code = responseBody.code;
		}
		throw error;
	}
	try {
		return await response.json();
	} catch {
		throw new Error('Failed to parse JSON response from API');
	}
}

function isStaleReadingSaveError(error: unknown) {
	return error instanceof Error
		&& (error as ReadingApiError).status === 409
		&& (error as ReadingApiError).code === 'STALE_READING_SAVE';
}

function buildReadingPositionBody(payload: ReadingSavePayload, saveSequence?: string, expectedSaveSequence?: string) {
	const positionBody = typeof payload.position === 'number'
		? { currentPage: payload.position, readStatus: payload.readStatus }
		: { currentPosition: payload.position, readStatus: payload.readStatus };
	const body = payload.progressPercentage === undefined
		? positionBody
		: { ...positionBody, progressPercentage: payload.progressPercentage };
	return saveSequence === undefined ? body : { ...body, saveSequence, ...(expectedSaveSequence === undefined ? {} : { expectedSaveSequence }) };
}

export function reserveReadingSave(
	bookId: number,
	position: number | string,
	readStatus: Exclude<ReadStatus, 'unread'> = 'reading',
	progressPercentage?: number | null,
	userId?: number,
	generation?: number,
) {
	const sessionUserId = getReadingUserId(userId);
	if (sessionUserId === null) return Promise.resolve(undefined);
	const requestGeneration = generation ?? readingSaveGeneration;
	const scopeKey = getReadingScopeKey(bookId, sessionUserId);
	return readScopedReadingSaveSequence(scopeKey).then(async (record) => {
		assertReadingSaveGeneration(requestGeneration);
		if (record?.serverSequence === undefined || normalizeReadingSaveSequence(record.serverSequence) === undefined) {
			await fetchAuthoritativeReadingInfo(bookId, sessionUserId, requestGeneration);
		}
		return reserveReadingSaveSequence({ position, readStatus, progressPercentage }, bookId, sessionUserId, requestGeneration);
	});
}

async function fetchAuthoritativeReadingInfo(bookId: number, userId: number, generation: number) {
	assertReadingSaveGeneration(generation);
	const result = await apiFetch<ReadingInfo>(`/api/v1/books/${bookId}/reading-info`, {
		headers: { [READING_SESSION_USER_HEADER]: String(userId) },
	});
	assertReadingSaveGeneration(generation);
	const serverSequence = normalizeReadingSaveSequence(result.saveSequence) ?? '0';
	await observeServerReadingSaveSequence(getReadingScopeKey(bookId, userId), serverSequence, generation);
	return { ...result, saveSequence: serverSequence };
}

export async function fetchReadingInfo(bookId: number, userId?: number): Promise<ReadingInfo> {
	const requestGeneration = readingSaveGeneration;
	const sessionUserId = getReadingUserId(userId);
	const scopeKey = getReadingScopeKey(bookId, sessionUserId ?? undefined);
	for (const flush of readingFlushes.get(scopeKey) ?? []) flush();
	await pendingSaves.get(scopeKey)?.catch(() => undefined);
	assertReadingSaveGeneration(requestGeneration);

	if (sessionUserId === null) {
		const readingInfo = await apiFetch<ReadingInfo>(`/api/v1/books/${bookId}/reading-info`);
		assertReadingSaveGeneration(requestGeneration);
		return readingInfo;
	}

	let readingInfo: ReadingInfo = await fetchAuthoritativeReadingInfo(bookId, sessionUserId, requestGeneration);
	const pending = await readPendingReadingSave(scopeKey).catch(() => null);
	if (!pending) return readingInfo;

	const pendingSequence = normalizeReadingSaveSequence(pending.saveSequence);
	const expectedBase = normalizeReadingSaveSequence(pending.expectedSaveSequence);
	const serverSequence = normalizeReadingSaveSequence(readingInfo.saveSequence);
	if (pending.sequenceVersion !== READING_SAVE_SEQUENCE_VERSION || pendingSequence === undefined
		|| expectedBase === undefined || serverSequence !== expectedBase) {
		if (pendingSequence !== undefined) await clearPendingReadingSave(scopeKey, pendingSequence).catch(() => undefined);
		return { ...readingInfo, saveConflict: true };
	}

	try {
		readingInfo = await saveReadingInfo(
			bookId,
			pending.position,
			pending.readStatus,
			pending.hasProgressPercentage ? pending.progressPercentage : undefined,
			sessionUserId,
			requestGeneration,
			false,
			pending.saveSequence,
		);
		await pendingSaves.get(scopeKey)?.catch(() => undefined);
		assertReadingSaveGeneration(requestGeneration);
		return readingInfo;
	} catch (error) {
		if (isStaleReadingSaveError(error)) {
			const authoritative = (error as ReadingApiError).authoritativeReadingInfo
				?? await fetchAuthoritativeReadingInfo(bookId, sessionUserId, requestGeneration);
			return { ...authoritative, saveConflict: true };
		}
		// Network/server failures retain the durable outbox and do not block reading.
		return readingInfo;
	}
}

// Keep writes scoped by account and book. A keepalive save is allowed to
// overtake an older request; the server sequence is what makes that safe.
const pendingSaves = new Map<string, Promise<ReadingInfo>>();
type PendingReadingSaveSubmission = {
	sequence?: string;
	expectedSequence?: string;
	generation: number;
	promise?: Promise<ReadingInfo>;
};
const pendingReadingSaveSubmissions = new Map<string, PendingReadingSaveSubmission>();
const readingFlushes = new Map<string, Set<() => void>>();

export function getReadingSaveGeneration() {
	return readingSaveGeneration;
}

export function invalidateReadingSaves() {
	readingSaveGeneration += 1;
	reservedReadingSaveBases.clear();
	pendingSaves.clear();
	pendingReadingSaveSubmissions.clear();
	readingFlushes.clear();
	for (const controller of inFlightReadingSaves) controller.abort();
	inFlightReadingSaves.clear();
	return Promise.resolve();
}

export function registerReadingFlush(bookId: number, flush: () => void, userId?: number) {
	const scopeKey = getReadingScopeKey(bookId, getReadingUserId(userId) ?? undefined);
	const callbacks = readingFlushes.get(scopeKey) ?? new Set<() => void>();
	callbacks.add(flush);
	readingFlushes.set(scopeKey, callbacks);
	return () => {
		callbacks.delete(flush);
		if (!callbacks.size) readingFlushes.delete(scopeKey);
	};
}

export async function flushReadingSaves(userId: number): Promise<boolean> {
	const sessionUserId = getReadingUserId(userId);
	if (sessionUserId === null) return true;
	const scopePrefix = `user:${sessionUserId}:book:`;
	let callbacksSucceeded = true;
	for (const [scopeKey, callbacks] of readingFlushes) {
		if (!scopeKey.startsWith(scopePrefix)) continue;
		for (const flush of callbacks) {
			try {
				flush();
			} catch {
				callbacksSucceeded = false;
			}
		}
	}
	const pending = Array.from(pendingSaves.entries())
		.filter(([scopeKey]) => scopeKey.startsWith(scopePrefix))
		.map(([, save]) => save);
	const results = await Promise.allSettled(pending);
	return callbacksSucceeded && results.every((result) => result.status === 'fulfilled');
}

export function saveReadingInfo(
	bookId: number,
	position: number | string,
	readStatus: Exclude<ReadStatus, 'unread'> = 'reading',
	progressPercentage?: number | null,
	userId?: number,
	generation?: number,
	keepalive = false,
	saveSequence?: ReadingSaveSequence,
): Promise<ReadingInfo> {
	const sessionUserId = getReadingUserId(userId);
	const requestGeneration = generation ?? readingSaveGeneration;
	const normalizedSaveSequence = saveSequence === undefined ? undefined : normalizeReadingSaveSequence(saveSequence);
	if (saveSequence !== undefined && normalizedSaveSequence === undefined) {
		return Promise.reject(new Error('Reading save sequence must be a safe non-negative integer'));
	}
	const payload: ReadingSavePayload = { position, readStatus, progressPercentage };
	const scopeKey = getReadingScopeKey(bookId, sessionUserId ?? undefined);
	const sequencePromise = normalizedSaveSequence !== undefined
		? Promise.resolve(normalizedSaveSequence)
		: reserveReadingSave(bookId, position, readStatus, progressPercentage, sessionUserId ?? undefined, requestGeneration);
	const previousSubmission = pendingReadingSaveSubmissions.get(scopeKey);
	const previous = pendingSaves.get(scopeKey) ?? Promise.resolve();
	const currentSubmission: PendingReadingSaveSubmission = { generation: requestGeneration };
	const headers: Record<string, string> = { 'Content-Type': 'application/json' };
	if (sessionUserId !== null) headers[READING_SESSION_USER_HEADER] = String(sessionUserId);
	const start = keepalive ? Promise.resolve() : previous.catch(() => undefined);
	let effectiveSequence: string | undefined;
	let effectiveExpectedSequence: string | undefined;
	let retriedAfterOwnAcknowledgement = false;
	const next = start.then(async () => {
		assertReadingSaveGeneration(requestGeneration);
		effectiveSequence = await sequencePromise;
		effectiveExpectedSequence = sessionUserId !== null && effectiveSequence !== undefined
			? await getExpectedReadingSaveSequence(scopeKey, effectiveSequence)
			: undefined;
		currentSubmission.sequence = effectiveSequence;
		currentSubmission.expectedSequence = effectiveExpectedSequence;
		assertReadingSaveGeneration(requestGeneration);
		while (true) {
			assertReadingSaveGeneration(requestGeneration);
			const body = buildReadingPositionBody(payload, effectiveSequence, effectiveExpectedSequence);
			const serializedBody = JSON.stringify(body);
			const canUseKeepalive = keepalive
				&& (typeof TextEncoder === 'undefined' || new TextEncoder().encode(serializedBody).byteLength <= KEEPALIVE_BODY_LIMIT_BYTES);
			const controller = new AbortController();
			inFlightReadingSaves.add(controller);
			try {
				const result = await patchReadingInfo(`/api/v1/books/${bookId}/reading-info`, {
					method: 'PATCH',
					headers,
					body: serializedBody,
					signal: controller.signal,
					...(canUseKeepalive ? { keepalive: true } : {}),
				});
				assertReadingSaveGeneration(requestGeneration);
				if (sessionUserId !== null && effectiveSequence !== undefined
					&& (result as ReadingInfo).saveSequence !== effectiveSequence) {
					throw new Error('Reading save acknowledgement did not confirm its sequence');
				}
				return result as ReadingInfo;
			} catch (error) {
				if (sessionUserId !== null && effectiveSequence !== undefined && isStaleReadingSaveError(error)) {
					assertReadingSaveGeneration(requestGeneration);
					if (keepalive && !retriedAfterOwnAcknowledgement
						&& previousSubmission?.generation === requestGeneration
						&& previousSubmission.sequence !== undefined
						&& previousSubmission.expectedSequence === effectiveExpectedSequence) {
						const previousResult = await previous?.catch(() => undefined);
						assertReadingSaveGeneration(requestGeneration);
						if (previousResult?.saveSequence === previousSubmission.sequence) {
							const pending = await readPendingReadingSave(scopeKey).catch(() => null);
							const pendingSequence = normalizeReadingSaveSequence(pending?.saveSequence);
							const pendingBase = normalizeReadingSaveSequence(pending?.expectedSaveSequence);
							if (pending && pendingSequence === effectiveSequence && pendingBase === previousSubmission.sequence) {
								const authoritative = await fetchAuthoritativeReadingInfo(bookId, sessionUserId, requestGeneration);
								if (authoritative.saveSequence === previousSubmission.sequence) {
									effectiveExpectedSequence = previousSubmission.sequence;
									currentSubmission.expectedSequence = effectiveExpectedSequence;
									retriedAfterOwnAcknowledgement = true;
									continue;
								}
								(error as ReadingApiError).authoritativeReadingInfo = authoritative;
							}
						}
					}
					const authoritative = await fetchAuthoritativeReadingInfo(bookId, sessionUserId, requestGeneration);
					const pending = await readPendingReadingSave(scopeKey).catch(() => null);
					if (pending && compareReadingSaveSequences(pending.saveSequence, effectiveSequence) === 0) {
						await clearPendingReadingSave(scopeKey, effectiveSequence).catch(() => undefined);
					}
					(error as ReadingApiError).authoritativeReadingInfo = authoritative;
				}
				throw error;
			} finally {
				inFlightReadingSaves.delete(controller);
			}
		}
	}).then(async (result) => {
		if (sessionUserId !== null && effectiveSequence !== undefined && effectiveExpectedSequence !== undefined) {
			await acknowledgeReadingSave(scopeKey, effectiveSequence, effectiveExpectedSequence, requestGeneration).catch(() => undefined);
		}
		return result;
	}).catch((error: unknown) => {
		if (effectiveSequence !== undefined && error instanceof Error && !isStaleReadingSaveError(error)) {
			(error as ReadingApiError).saveSequence = effectiveSequence;
		}
		throw error;
	});
	pendingSaves.set(scopeKey, next);
	currentSubmission.promise = next;
	pendingReadingSaveSubmissions.set(scopeKey, currentSubmission);
	void next.finally(() => {
		if (pendingSaves.get(scopeKey) === next) pendingSaves.delete(scopeKey);
		if (pendingReadingSaveSubmissions.get(scopeKey) === currentSubmission) pendingReadingSaveSubmissions.delete(scopeKey);
	}).catch(() => undefined);
	return next;
}
