declare module 'epubjs/src/managers/views/iframe' {
	import type Contents from 'epubjs/types/contents';
	export default class IframeView {
		load(content: string): Promise<Contents>;
	}
}

declare module 'epubjs/src/rendition' {
	import Rendition from 'epubjs/types/rendition';
	export default Rendition;
}

declare module 'epubjs/src/epubcfi' {
	import EpubCFI from 'epubjs/types/epubcfi';
	export default EpubCFI;
}

declare module 'epubjs/src/managers/default' {
	export default class DefaultManager {
		settings: { axis: string };
		isPaginated: boolean;
		layout: { name: string; delta: number; height: number; spread: (value: string) => void };
		viewSettings: { width: number; height: number };
		views?: { forEach: (callback: (view: { contents?: import('epubjs/types/contents').default; size: (width: number, height: number) => void }) => void) => void };
		container: HTMLElement;
		resize(width?: number, height?: number, cfi?: string): void;
		clear(): void;
		updateLayout(): void;
		moveTo(offset: { left: number; top: number }, width: number): void;
		scrollTo(x: number, y: number, silent?: boolean): void;
		scrollBy(x: number, y: number, silent?: boolean): void;
		onScroll(): void;
		currentLocation(): Array<{ index: number; pages: number[]; totalPages: number; mapping: { start: string; end: string } }>;
		visible(): Array<{ section: { index: number; cfiBase: string }; contents: import('epubjs/types/contents').default; position: () => DOMRect; height: () => number }>;
	}
}
