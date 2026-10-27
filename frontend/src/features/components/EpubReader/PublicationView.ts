import IframeView from 'epubjs/src/managers/views/iframe';

// epub.js detects pagination axes from <html>. Some valid publications put
// writing-mode on <body> only. Read its effective mode before the first layout,
// so sizing, mapping and navigation all use the same axis as the visible text.
export class PublicationView extends IframeView {
	override async load(content: string) {
		const contents = await super.load(content);
		const doc = contents.document;
		const mode = doc.defaultView?.getComputedStyle(doc.body).writingMode;
		if (mode) contents.writingMode(mode);
		return contents;
	}
}
