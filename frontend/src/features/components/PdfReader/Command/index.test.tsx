import { describe, expect, test, vi } from 'vitest';
import { render } from 'vitest-browser-react';
import CommandButton from './index';

const state = {
	currentPage: 2,
	totalPages: 5,
};

const createCommandProps = (currentPage: number, totalPages: number) => ({
	state: { currentPage, totalPages },
	pageInput: String(currentPage),
	setPageInput: vi.fn(),
	handleGoToPage: vi.fn(),
	prevResolved: {
		visible: true,
		disabled: false,
		execute: vi.fn(),
		label: '<',
	},
	nextResolved: {
		visible: true,
		disabled: false,
		execute: vi.fn(),
		label: '>',
	},
});

describe('CommandButton', () => {
	test('renders page navigation state', async () => {
		const { getByText, getByRole } = await render(
			<CommandButton
				state={state}
				pageInput='4'
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
		await expect.element(getByRole('spinbutton')).toHaveValue(4);
		await expect.element(getByText('/ 5', { exact: true })).toBeInTheDocument();
		await expect.element(getByText('2 / 5 ページ 40%')).toBeInTheDocument();
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

		await getByRole('button', { name: '前のページへ' }).click();
		await getByRole('button', { name: '次のページへ' }).click();

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

	test.each([
		{ currentPage: 1, totalPages: 7, percentage: 14 },
		{ currentPage: 2, totalPages: 7, percentage: 29 },
		{ currentPage: 5, totalPages: 8, percentage: 63 },
		{ currentPage: 7, totalPages: 7, percentage: 100 },
	])('shows a rounded percentage for $currentPage / $totalPages pages', async ({ currentPage, totalPages, percentage }) => {
		const { getByRole, getByText } = await render(
			<CommandButton {...createCommandProps(currentPage, totalPages)} />,
		);

		await expect.element(getByRole('status')).toHaveTextContent(
			`${currentPage} / ${totalPages} ページ ${percentage}%`,
		);
		await expect.element(getByText(`/ ${totalPages}`, { exact: true })).toBeInTheDocument();
	});

	test('omits the percentage when there are no pages', async () => {
		const { getByRole, getByText } = await render(
			<CommandButton {...createCommandProps(0, 0)} />,
		);

		await expect.element(getByRole('status')).not.toBeInTheDocument();
		await expect.element(getByText('/ 0', { exact: true })).toBeInTheDocument();
	});
});
