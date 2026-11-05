import { afterEach, describe, expect, test, vi } from 'vitest';
import { cleanup, render } from 'vitest-browser-react';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from './sheetCapture';

describe('reader-only outline and display-settings sheets', () => {
	afterEach(async () => {
		await cleanup();
	});

	test('keeps both EPUB and PDF right-side sheets inside the compact width limit', async () => {
		const oldReaderWidthClasses = [
			'w-[min(88vw,22rem)]',
			'w-[calc(100vw-2rem)] max-w-sm',
		];

		for (const className of oldReaderWidthClasses) {
			await render(
				<Sheet open>
					<SheetContent side='right' className={className}>
						<SheetHeader>
							<SheetTitle>この書籍の目次</SheetTitle>
							<SheetDescription>読書用サイドパネル</SheetDescription>
						</SheetHeader>
					</SheetContent>
				</Sheet>,
			);
			const panel = document.querySelector<HTMLElement>('[data-slot="sheet-content"][data-side="right"]');
			expect(panel).not.toBeNull();
			const expectedWidth = window.innerWidth >= 640 ? 224 : Math.min(208, window.innerWidth - 16);
			await vi.waitFor(() => {
				expect(panel!.getBoundingClientRect().width).toBeCloseTo(expectedWidth, 0);
			});
			expect(panel!.getBoundingClientRect().right).toBeLessThanOrEqual(window.innerWidth + 1);
			await cleanup();
		}
	});
});
