import { afterEach, describe, expect, test, vi } from 'vitest';
import { cleanup, render } from 'vitest-browser-react';
import ReadingSaveStatus from './index';

describe('ReadingSaveStatus compact reader header status', () => {
	afterEach(async () => {
		await cleanup();
	});

	test('unknown state stays explicit and never claims the fresh position was saved', async () => {
		const { container } = await render(<ReadingSaveStatus status='unknown' retry={vi.fn()} />);
		const element = container.querySelector('[role="status"]');
		expect(element?.querySelector('[aria-hidden="true"]')?.getAttribute('title')).toBe('読書位置の保存状態を確認できていません。');
		expect(element?.querySelector('[aria-hidden="true"]')?.textContent).toBe('未確認');
		expect(element?.querySelector('.sr-only')?.textContent).toBe('読書位置の保存状態を確認できていません。');
		expect(element?.textContent).not.toContain('保存済み');
	});

	test('saved state shows a short one-line label but preserves the full accessible announcement', async () => {
		const { container } = await render(<ReadingSaveStatus status='saved' retry={vi.fn()} />);
		const element = container.querySelector('[role="status"]');
		expect(element?.hasAttribute('title')).toBe(false);
		expect(element?.querySelector('[aria-hidden="true"]')?.getAttribute('title')).toBe('読書位置を保存済み');
		expect(element?.querySelector('[aria-hidden="true"]')?.textContent).toBe('✓ 保存済み');
		expect(element?.querySelector('.sr-only')?.textContent).toBe('読書位置を保存済み');
		expect(element?.className).toContain('min-h-11');
	});

	test('saving state never claims saved', async () => {
		const { container } = await render(<ReadingSaveStatus status='saving' retry={vi.fn()} />);
		const element = container.querySelector('[role="status"]');
		expect(element?.hasAttribute('title')).toBe(false);
		expect(element?.querySelector('[aria-hidden="true"]')?.getAttribute('title')).toBe('読書位置を保存中…');
		expect(element?.querySelector('[aria-hidden="true"]')?.textContent).toBe('保存中…');
		expect(element?.querySelector('.sr-only')?.textContent).toBe('読書位置を保存中…');
		expect(element?.textContent).not.toContain('保存済み');
	});

	test('error remains visible, announces failure and invokes retry once', async () => {
		const retry = vi.fn();
		const { container } = await render(<ReadingSaveStatus status='error' retry={retry} />);
		const element = container.querySelector('[role="alert"]');
		expect(element?.hasAttribute('title')).toBe(false);
		expect(element?.querySelector('[aria-hidden="true"]')?.getAttribute('title')).toBe('読書位置を保存できませんでした。');
		expect(element?.querySelector('[aria-hidden="true"]')?.textContent).toBe('保存失敗');
		expect(element?.querySelector('.sr-only')?.textContent).toBe('読書位置を保存できませんでした。');
		const button = element?.querySelector('button');
		expect(button?.textContent?.trim()).toBe('再試行');
		button?.click();
		expect(retry).toHaveBeenCalledTimes(1);
	});
});
