import { afterEach, describe, expect, test, vi } from 'vitest';
import { renderHook } from 'vitest-browser-react';
import { useIsMobile } from './use-mobile';

const setInnerWidth = (width: number) => {
	Object.defineProperty(window, 'innerWidth', {
		configurable: true,
		value: width,
	});
};

const createMatchMediaMock = ({
	matches,
	addEventListener = vi.fn(),
	removeEventListener = vi.fn(),
}: {
	matches: boolean;
	addEventListener?: ReturnType<typeof vi.fn>;
	removeEventListener?: ReturnType<typeof vi.fn>;
}) =>
	({
		matches,
		media: '(max-width: 767px)',
		addEventListener,
		removeEventListener,
	} as unknown as MediaQueryList);

afterEach(() => {
	vi.clearAllMocks();
	vi.restoreAllMocks();
});

describe('useIsMobile', () => {
	test('useIsMobile returns true below the mobile breakpoint', async () => {
		const addEventListener = vi.fn();
		const removeEventListener = vi.fn();
		setInnerWidth(767);
		vi.spyOn(window, 'matchMedia').mockReturnValue(createMatchMediaMock({
			matches: true,
			addEventListener,
			removeEventListener,
		}));

		const { result, unmount } = await renderHook(() => useIsMobile());

		await expect.poll(() => result.current).toBe(true);
		expect(window.matchMedia).toHaveBeenCalledWith('(max-width: 767px)');
		expect(addEventListener).toHaveBeenCalledWith('change', expect.any(Function));

		await unmount();

		expect(removeEventListener).toHaveBeenCalledWith('change', expect.any(Function));
	});

	test('useIsMobile updates when the media query change listener runs', async () => {
		let changeListener: (() => void) | undefined;
		setInnerWidth(1024);
		vi.spyOn(window, 'matchMedia').mockReturnValue(createMatchMediaMock({
			matches: false,
			addEventListener: vi.fn((eventName: string, listener: () => void) => {
				if (eventName === 'change') changeListener = listener;
			}),
			removeEventListener: vi.fn(),
		}));

		const { result, act } = await renderHook(() => useIsMobile());

		await expect.poll(() => result.current).toBe(false);

		setInnerWidth(500);
		await act(() => {
			changeListener?.();
		});

		expect(result.current).toBe(true);
	});
});
