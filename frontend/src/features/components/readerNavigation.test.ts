import { describe, expect, test, vi } from 'vitest';
import { attachEdgeTapNavigation, getEdgeTurnAction } from './readerNavigation';

describe('reader edge tap navigation', () => {
	test('軽い端タップは一度だけ遅延実行する', () => {
		vi.useFakeTimers();
		const host = document.createElement('div');
		const onTurn = vi.fn();
		const pointerUp = vi.fn();
		window.addEventListener('pointerup', pointerUp, { capture: true });
		const cleanup = attachEdgeTapNavigation(host, {
			direction: 'ltr',
			getBounds: () => ({ left: 0, width: 100 }),
			getSelectionText: () => '',
			onTurn,
		});

		host.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerId: 1, isPrimary: true, clientX: 99 }));
		host.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 1, isPrimary: true, clientX: 99 }));
		expect(onTurn).not.toHaveBeenCalled();
		vi.advanceTimersByTime(220);
		expect(onTurn).toHaveBeenCalledOnce();
		expect(onTurn).toHaveBeenCalledWith('next');
		expect(pointerUp).toHaveBeenCalledOnce();

		cleanup();
		window.removeEventListener('pointerup', pointerUp, { capture: true });
		vi.useRealTimers();
	});

	test('ダブルクリックになった端操作はページを送らない', () => {
		vi.useFakeTimers();
		const host = document.createElement('div');
		const onTurn = vi.fn();
		const cleanup = attachEdgeTapNavigation(host, {
			direction: 'ltr',
			getBounds: () => ({ left: 0, width: 100 }),
			getSelectionText: () => '',
			onTurn,
		});

		host.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerId: 1, isPrimary: true, clientX: 99 }));
		host.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 1, isPrimary: true, clientX: 99 }));
		host.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
		vi.advanceTimersByTime(500);
		expect(onTurn).not.toHaveBeenCalled();

		cleanup();
		vi.useRealTimers();
	});

	test('15%の左右端をLTR/RTLのページ方向へ変換する', () => {
		expect(getEdgeTurnAction(10, 0, 100, 'ltr')).toBe('previous');
		expect(getEdgeTurnAction(90, 0, 100, 'ltr')).toBe('next');
		expect(getEdgeTurnAction(10, 0, 100, 'rtl')).toBe('next');
		expect(getEdgeTurnAction(90, 0, 100, 'rtl')).toBe('previous');
		expect(getEdgeTurnAction(50, 0, 100, 'ltr')).toBeNull();
	});

	test('文字選択、リンク、マルチタッチではページを送らない', () => {
		const host = document.createElement('div');
		const link = document.createElement('a');
		host.append(link);
		document.body.append(host);
		const onTurn = vi.fn();
		let selection = '';
		const cleanup = attachEdgeTapNavigation(host, {
			direction: 'ltr',
			getBounds: () => ({ left: 0, width: 100 }),
			getSelectionText: () => selection,
			onTurn,
		});

		link.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerId: 1, isPrimary: true, clientX: 99 }));
		link.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 1, isPrimary: true, clientX: 99 }));
		selection = 'selected';
		host.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerId: 2, isPrimary: true, clientX: 99 }));
		host.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 2, isPrimary: true, clientX: 99 }));
		selection = '';
		host.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerId: 3, isPrimary: true, clientX: 99 }));
		host.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerId: 4, isPrimary: false, clientX: 50 }));
		host.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 3, isPrimary: true, clientX: 99 }));

		expect(onTurn).not.toHaveBeenCalled();
		cleanup();
		host.remove();
	});
});
