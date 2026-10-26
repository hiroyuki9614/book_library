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
		<div className='flex items-center justify-center gap-2'>
			<Button onClick={zoomProvides.zoomOut} className='size-11 p-0 text-lg' title='縮小' aria-label='縮小'>-</Button>
			<span className='min-w-12 text-center text-sm'>{Math.round(zoomState.currentZoomLevel * 100)}%</span>
			<Button onClick={zoomProvides.zoomIn} className='size-11 p-0 text-lg' title='拡大' aria-label='拡大'>+</Button>
			<Button onClick={() => zoomProvides.requestZoom(1.0)} className='min-h-11 px-3' aria-label='倍率をリセット'>Reset</Button>
		</div>
	);
};
