import { useZoom } from '@embedpdf/plugin-zoom/react';
import { Button } from '@/components/ui/button';

interface ZoomToolbarProps {
	documentId: string;
}

export const ZoomToolbar = ({ documentId }: ZoomToolbarProps) => {
	const { provides: zoomProvides, state: zoomState } = useZoom(documentId);

	if (!zoomProvides) {
		return null;
	}

	return (
		<div>
			<Button onClick={zoomProvides.zoomOut}>-</Button>
			<span>{Math.round(zoomState.currentZoomLevel * 100)}%</span>
			<Button onClick={zoomProvides.zoomIn}>+</Button>
			<Button onClick={() => zoomProvides.requestZoom(1.0)}>Reset</Button>
		</div>
	);
};
