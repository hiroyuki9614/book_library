import IframeView from 'epubjs/src/managers/views/iframe';

// epub.js normally derives its pagination axis from <html>. Some valid EPUBs
// declare writing-mode on <body>, so detect the effective mode before the
// first layout. CFI mapping and the visible pagination then share one axis.
export class PublicationView extends IframeView {
	override async load(content: string) {
		const contents = await super.load(content);
		const document = contents.document;
		const writingMode = document.defaultView?.getComputedStyle(document.body).writingMode;
		if (writingMode) contents.writingMode(writingMode);
		return contents;
	}
}
