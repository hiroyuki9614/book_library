import DefaultManager from 'epubjs/src/managers/default';
import EpubCFI from 'epubjs/src/epubcfi';

// Vertical pagination advances by the viewport height. epub.js otherwise resets
// delta to the horizontal width during location measurement and snaps CFI restores
// to the wrong grid, even though next/prev advance by height.
export class PublicationManager extends DefaultManager {
	private readingScroll?: {left: number; top: number};
	private retainingViews = false;

	override resize(width?: number, height?: number, cfi?: string) {
		// Native resize destroys every iframe, reparsing and laying out a whole
		// long chapter twice when switching spreads. Reflow existing documents;
		// rendition's resized handler still restores the CFI on the new grid.
		this.retainingViews = this.isPaginated && this.layout?.name === 'reflowable';
		const writingModes = new Map<unknown, string>();
		const retainedViews: Array<{ contents?: { document: Document }; size: (width: number, height: number) => void }> = [];
		this.views?.forEach(view => {
			retainedViews.push(view);
			try {
				const body = view.contents?.document.body;
				if (body) writingModes.set(view, getComputedStyle(body).writingMode);
				for (const sheet of view.contents?.document.styleSheets ?? []) {
					// The reader's own text-inset stylesheet contains direction rules,
					// but it cannot change the publication axis.
					// Only publisher CSS should trigger a native view rebuild here.
					if ((sheet.ownerNode as HTMLElement | null)?.id === 'belib-text-direction') continue;
					// An unreadable stylesheet can change the writing axis without being
					// observable here; let epub.js rebuild in that case.
					const rules = [...sheet.cssRules].map((rule) => rule.cssText).join('\n');
					if (/@(?:media|container)\b/i.test(rules) && /writing-mode|direction/i.test(rules)) this.retainingViews = false;
				}
			} catch { this.retainingViews = false; }
		});
		const previous = {width: this.viewSettings.width, height: this.viewSettings.height};
		try {
			super.resize(width, height, cfi);
			if (this.retainingViews && (previous.width !== this.viewSettings.width || previous.height !== this.viewSettings.height)) {
				retainedViews.forEach(view => view.size(this.viewSettings.width, this.viewSettings.height));
				for (const view of retainedViews) {
					const body = view.contents?.document.body;
					if (body && writingModes.get(view) !== getComputedStyle(body).writingMode) {
						this.retainingViews = false;
						break;
					}
				}
				if (!this.retainingViews) super.resize(width, height, cfi);
			}
		} finally {
			this.retainingViews = false;
		}
	}

	override clear() {
		if (!this.retainingViews) super.clear();
	}

	private rememberScroll() {
		if (this.container) this.readingScroll = {left: this.container.scrollLeft, top: this.container.scrollTop};
	}

	override scrollTo(x: number, y: number, silent?: boolean) {
		super.scrollTo(x, y, silent);
		this.rememberScroll();
	}

	override scrollBy(x: number, y: number, silent?: boolean) {
		super.scrollBy(x, y, silent);
		this.rememberScroll();
	}

	override onScroll() {
		// Chromium scrolls the pagination container to reveal the selection's text
		// node when Shift+Arrow expands a native selection. A node can span several
		// Japanese pages, so that scroll can land between columns and rewind the CFI.
		// Keep the last explicit reader offset while leaving the selection intact.
		const offset = this.readingScroll;
		if (this.isPaginated && offset && this.visible().some(view => view.contents.document.getSelection()?.isCollapsed === false)
			&& (Math.abs(this.container.scrollLeft - offset.left) > .5 || Math.abs(this.container.scrollTop - offset.top) > .5)) {
			super.scrollTo(offset.left, offset.top, true);
			this.rememberScroll();
			return;
		}
		super.onScroll();
		this.rememberScroll();
	}

	override updateLayout() {
		// Vertical spreads are composed from two single-page managers by the
		// adapter. Horizontal chapters use epub.js's native spread layout, so do
		// not erase the requested divisor=2 when their layout is updated.
		if (this.settings.axis === 'vertical') this.layout?.spread('none');
		super.updateLayout();
		if (this.settings.axis === 'vertical' && this.isPaginated && this.layout) this.layout.delta = this.layout.height;
	}

	override moveTo(offset: { left: number; top: number }, width: number) {
		if (this.settings.axis !== 'vertical' || !this.isPaginated) return super.moveTo(offset, width);
		const height = this.layout.height;
		const top = Math.floor((offset.top + 0.5) / height) * height;
		this.scrollTo(0, Math.max(0, Math.min(top, this.container.scrollHeight - height)), true);
	}

	override currentLocation() {
		const rows = super.currentLocation();
		const viewport = this.container.getBoundingClientRect();
		for (const row of rows) {
			const view = this.visible().find((v) => v.section.index === row.index);
			if (!view?.contents?.document.body) continue;
			if (this.isPaginated && this.settings.axis === 'vertical') {
				const start = Math.max(0, viewport.top - view.position().top);
				const total = Math.ceil(view.height() / this.layout.height);
				const first = Math.floor((start + 0.5) / this.layout.height) + 1;
				const last = Math.min(total, Math.ceil((start + viewport.height - 0.5) / this.layout.height));
				row.totalPages = total;
				row.pages = Array.from({ length: last - first + 1 }, (_, i) => first + i);
			}
			// Native mapping splits at spaces. A Japanese text node may span pages
			// without spaces, so its CFI can point into the preceding screen. Map
			// the first/last visible glyph instead, without changing source nodes.
			const doc = view.contents.document;
			const iframe = doc.defaultView?.frameElement;
			if (!iframe) continue;
			const frame = iframe.getBoundingClientRect();
			// Range boxes commonly touch a viewport edge even when the glyph ink is
			// fully visible (notably the first vertical heading character). Positive
			// intersection is the stable boundary; a one-pixel inset skips that glyph.
			const inside = (r: DOMRect) => r.right + frame.left >= viewport.left && r.left + frame.left < viewport.right && r.bottom + frame.top >= viewport.top && r.top + frame.top < viewport.bottom;
			const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
			let firstRange: Range | undefined;
			let lastRange: Range | undefined;
			const boundary = (node: Node, start: number, end: number, first: boolean): Range | undefined => {
				if (start >= end) return;
				const range = doc.createRange(); range.setStart(node, start); range.setEnd(node, end);
				if (![...range.getClientRects()].some(inside)) return;
				if (end - start === 1) return /\s/.test(node.textContent![start]) ? undefined : range;
				const middle = Math.floor((start + end) / 2);
				return first
					? boundary(node, start, middle, true) ?? boundary(node, middle, end, true)
					: boundary(node, middle, end, false) ?? boundary(node, start, middle, false);
			};
			const candidates: Node[] = [];
			while (walker.nextNode()) {
				const node = walker.currentNode;
				if (!node.textContent?.trim() || node.parentElement?.closest('rt,rp,script,style')) continue;
				const range = doc.createRange();
				range.selectNodeContents(node);
				if (![...range.getClientRects()].some(inside)) continue;
				if (node.parentElement && doc.defaultView?.getComputedStyle(node.parentElement).visibility === 'hidden') continue;
				firstRange ??= boundary(node, 0, node.textContent.length, true);
				candidates.push(node);
			}
			for (let i = candidates.length - 1; i >= 0 && !lastRange; i--) lastRange = boundary(candidates[i], 0, candidates[i].textContent!.length, false);
			if (firstRange && lastRange) {
				firstRange.collapse(true); lastRange.collapse(false);
				row.mapping = { start: new EpubCFI(firstRange, view.section.cfiBase).toString(), end: new EpubCFI(lastRange, view.section.cfiBase).toString() };
			}
		}
		return rows;
	}
}
