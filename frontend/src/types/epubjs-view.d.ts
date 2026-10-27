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
		layout: { delta: number; height: number; spread: (value: string) => void };
		container: HTMLElement;
		updateLayout(): void;
		moveTo(offset: { left: number; top: number }, width: number): void;
		scrollTo(x: number, y: number, silent?: boolean): void;
		currentLocation(): Array<{ index: number; pages: number[]; totalPages: number; mapping: { start: string; end: string } }>;
		visible(): Array<{ section: { index: number; cfiBase: string }; contents: import('epubjs/types/contents').default; position: () => DOMRect; height: () => number }>;
	}
}
