import { describe, expect, test, vi } from 'vitest';
import { render } from 'vitest-browser-react';
import { ZoomToolbar } from './index';

const zoomState = vi.hoisted(() => ({
	zoomIn: vi.fn(),
	zoomOut: vi.fn(),
	requestZoom: vi.fn(),
	currentZoomLevel: 1.25,
	providesZoomControls: true,
}));

vi.mock('@embedpdf/plugin-zoom/react', () => ({
	useZoom: vi.fn(() => ({
		provides: zoomState.providesZoomControls
			? {
					zoomIn: zoomState.zoomIn,
					zoomOut: zoomState.zoomOut,
					requestZoom: zoomState.requestZoom,
				}
			: null,
		state: {
			currentZoomLevel: zoomState.currentZoomLevel,
		},
	})),
}));

describe('ZoomToolbar', () => {
	test('renders the current zoom percentage', async () => {
		zoomState.currentZoomLevel = 1.25;
		zoomState.providesZoomControls = true;

		const { getByText } = await render(<ZoomToolbar documentId='document-1' />);

		await expect.element(getByText('125%')).toBeInTheDocument();
	});

	test('calls zoom controls', async () => {
		zoomState.zoomIn.mockClear();
		zoomState.zoomOut.mockClear();
		zoomState.requestZoom.mockClear();
		zoomState.providesZoomControls = true;

		const { getByRole } = await render(<ZoomToolbar documentId='document-1' />);

		await getByRole('button', { name: '-' }).click();
		await getByRole('button', { name: '+' }).click();
		await getByRole('button', { name: 'Reset' }).click();

		expect(zoomState.zoomOut).toHaveBeenCalledTimes(1);
		expect(zoomState.zoomIn).toHaveBeenCalledTimes(1);
		expect(zoomState.requestZoom).toHaveBeenCalledWith(1);
	});

	test('renders nothing until zoom controls are available', async () => {
		zoomState.providesZoomControls = false;

		const { getByText } = await render(<ZoomToolbar documentId='document-1' />);

		await expect.element(getByText('Reset')).not.toBeInTheDocument();
	});
});
