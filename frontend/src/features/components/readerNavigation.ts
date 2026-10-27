export type PageTurnDirection = 'ltr' | 'rtl';
export type PageTurnAction = 'previous' | 'next';

type PointerSnapshot = {
	pointerId: number;
	x: number;
	y: number;
	startedAt: number;
	target: EventTarget | null;
	cancelled: boolean;
};

type EdgeTapOptions = {
	direction: PageTurnDirection;
	getBounds: () => { left: number; width: number };
	getSelectionText: () => string;
	onTurn: (action: PageTurnAction) => void;
	isDisabled?: () => boolean;
	edgeRatio?: number;
};

const INTERACTIVE_SELECTOR = [
	'a',
	'button',
	'input',
	'textarea',
	'select',
	'option',
	'label',
	'[role="button"]',
	'[contenteditable="true"]',
	'[data-reader-tap-ignore]',
].join(',');

export function getEdgeTurnAction(
	x: number,
	left: number,
	width: number,
	direction: PageTurnDirection,
	edgeRatio = 0.15,
): PageTurnAction | null {
	if (!Number.isFinite(width) || width <= 0) return null;

	const offset = x - left;
	if (offset < 0 || offset > width) return null;

	if (offset <= width * edgeRatio) {
		return direction === 'rtl' ? 'next' : 'previous';
	}
	if (offset >= width * (1 - edgeRatio)) {
		return direction === 'rtl' ? 'previous' : 'next';
	}
	return null;
}

export function isReaderInteractiveTarget(target: EventTarget | null) {
	if (!target || typeof (target as Element).closest !== 'function') return false;
	return Boolean((target as Element).closest(INTERACTIVE_SELECTOR));
}

export function attachEdgeTapNavigation(target: Document | HTMLElement, options: EdgeTapOptions) {
	const activePointers = new Set<number>();
	let pointer: PointerSnapshot | null = null;
	let lastTurnAt = Number.NEGATIVE_INFINITY;

	const handlePointerDown = (event: PointerEvent) => {
		activePointers.add(event.pointerId);
		if (activePointers.size > 1 && pointer) {
			pointer.cancelled = true;
		}
		if (event.button !== 0 || event.isPrimary === false || options.isDisabled?.()) return;

		pointer = {
			pointerId: event.pointerId,
			x: event.clientX,
			y: event.clientY,
			startedAt: performance.now(),
			target: event.target,
			cancelled: activePointers.size > 1,
		};
	};

	const handlePointerUp = (event: PointerEvent) => {
		activePointers.delete(event.pointerId);
		const candidate = pointer;
		if (!candidate || candidate.pointerId !== event.pointerId) return;
		pointer = null;

		if (
			candidate.cancelled
			|| options.isDisabled?.()
			|| isReaderInteractiveTarget(candidate.target)
			|| Math.hypot(event.clientX - candidate.x, event.clientY - candidate.y) > 12
			|| performance.now() - candidate.startedAt > 600
			|| options.getSelectionText().trim()
		) {
			return;
		}

		const now = performance.now();
		if (now - lastTurnAt < 250) return;

		const bounds = options.getBounds();
		const action = getEdgeTurnAction(event.clientX, bounds.left, bounds.width, options.direction, options.edgeRatio);
		if (!action) return;

		lastTurnAt = now;
		options.onTurn(action);
	};

	const handlePointerCancel = (event: PointerEvent) => {
		activePointers.delete(event.pointerId);
		if (pointer?.pointerId === event.pointerId) pointer = null;
	};

	target.addEventListener('pointerdown', handlePointerDown as EventListener, { passive: true });
	target.addEventListener('pointerup', handlePointerUp as EventListener, { passive: true });
	target.addEventListener('pointercancel', handlePointerCancel as EventListener, { passive: true });

	return () => {
		target.removeEventListener('pointerdown', handlePointerDown as EventListener);
		target.removeEventListener('pointerup', handlePointerUp as EventListener);
		target.removeEventListener('pointercancel', handlePointerCancel as EventListener);
	};
}
