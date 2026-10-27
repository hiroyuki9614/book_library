declare module 'epubjs/src/managers/views/iframe' {
	import type Contents from 'epubjs/types/contents';
	export default class IframeView {
		load(content: string): Promise<Contents>;
	}
}
