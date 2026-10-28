import { useZoom } from '@embedpdf/plugin-zoom/react';
import { ZoomMode } from '@embedpdf/plugin-zoom';
import { Button } from '@/components/ui/button';

interface ZoomToolbarProps {
	documentId: string;
	compact?: boolean;
}

export const ZoomToolbar = ({ documentId, compact = false }: ZoomToolbarProps) => {
	const { provides: zoomProvides, state: zoomState } = useZoom(documentId);

	if (!zoomProvides) {
		return null;
	}

	return (
		<div className='flex items-center justify-center gap-2'>
			<Button onClick={zoomProvides.zoomOut} className='size-11 p-0 text-lg' title='縮小' aria-label='縮小'>-</Button>
			<span className='min-w-12 text-center text-sm'>{Math.round(zoomState.currentZoomLevel * 100)}%</span>
			<Button onClick={zoomProvides.zoomIn} className='size-11 p-0 text-lg' title='拡大' aria-label='拡大'>+</Button>
			<Button onClick={() => zoomProvides.requestZoom(compact ? ZoomMode.FitWidth : 1.0)} className='min-h-11 px-3' aria-label='倍率をリセット'>{compact ? 'ページ幅に合わせる' : 'Reset'}</Button>
		</div>
	);
};
