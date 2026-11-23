import IframeView from 'epubjs/src/managers/views/iframe';
import { preserveTextDirection } from './publication';

// epub.js detects pagination axes from <html>. Some valid publications put
// writing-mode on <body> only. Read its effective mode before the first layout,
// so sizing, mapping and navigation all use the same axis as the visible text.
export class PublicationView extends IframeView {
	declare settings: { direction?: 'ltr' | 'rtl' };

	override async load(content: string) {
		const contents = await super.load(content);
		const doc = contents.document;
		const mode = doc.defaultView?.getComputedStyle(doc.body).writingMode;
		if (mode) contents.writingMode(mode);
		if (mode === 'horizontal-tb') {
			// Capture publisher bidi before pagination changes the root/body.
			preserveTextDirection(doc, doc);
			const columns = contents.columns.bind(contents);
			contents.columns = (width, height, columnWidth, gap, dir) => {
				const direction = this.settings.direction ?? dir;
				// Rendition.direction() updates the manager, but epub.js's Layout
				// retains OPF direction, and columns() only sets the root for RTL.
				// Body CSS also controls column order. Keep all three aligned on
				// every format/reflow; text blocks retain their publisher bidi.
				contents.direction(direction);
				doc.body.style.setProperty('direction', direction, 'important');
				columns(width, height, columnWidth, gap, direction);
				// Apply after columns(), which writes normal inline direction for RTL.
				doc.documentElement.style.setProperty('direction', direction, 'important');
			};
		}
		return contents;
	}
}
