import { describe, expect, test, vi } from 'vitest';
import { render } from 'vitest-browser-react';
import CommandButton from './index';

const state = {
	currentPage: 2,
	totalPages: 5,
};

describe('CommandButton', () => {
	test('renders page navigation state', async () => {
		const { getByText, getByRole } = await render(
			<CommandButton
				state={state}
				pageInput='2'
				setPageInput={vi.fn()}
				handleGoToPage={vi.fn()}
				prevResolved={{
					visible: true,
					disabled: false,
					execute: vi.fn(),
					label: '<',
					shortcuts: ['arrowleft'],
				}}
				nextResolved={{
					visible: true,
					disabled: false,
					execute: vi.fn(),
					label: '>',
					shortcuts: ['arrowright'],
				}}
			/>,
		);

		await expect.element(getByText('Page')).toBeInTheDocument();
		await expect.element(getByRole('spinbutton')).toHaveValue(2);
		await expect.element(getByText('of 5')).toBeInTheDocument();
	});

	test('executes visible navigation commands', async () => {
		const executePrev = vi.fn();
		const executeNext = vi.fn();
		const { getByRole } = await render(
			<CommandButton
				state={state}
				pageInput='2'
				setPageInput={vi.fn()}
				handleGoToPage={vi.fn()}
				prevResolved={{
					visible: true,
					disabled: false,
					execute: executePrev,
					label: '<',
				}}
				nextResolved={{
					visible: true,
					disabled: false,
					execute: executeNext,
					label: '>',
				}}
			/>,
		);

		await getByRole('button', { name: '<' }).click();
		await getByRole('button', { name: '>' }).click();

		expect(executePrev).toHaveBeenCalledTimes(1);
		expect(executeNext).toHaveBeenCalledTimes(1);
	});

	test('does not render when the previous command is hidden', async () => {
		const { getByText } = await render(
			<CommandButton
				state={state}
				pageInput='2'
				setPageInput={vi.fn()}
				handleGoToPage={vi.fn()}
				prevResolved={{
					visible: false,
					disabled: false,
					execute: vi.fn(),
					label: '<',
				}}
				nextResolved={{
					visible: true,
					disabled: false,
					execute: vi.fn(),
					label: '>',
				}}
			/>,
		);

		await expect.element(getByText('Page')).not.toBeInTheDocument();
	});
});
