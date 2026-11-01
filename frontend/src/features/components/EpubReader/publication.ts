export type TocItem = { href: string; label: string; subitems?: TocItem[] };
export type TocEntry = { href: string; label: string; depth: number };

export function flattenToc(items: TocItem[], depth = 0): TocEntry[] {
	return items.flatMap((item) => [{ href: item.href, label: item.label, depth }, ...flattenToc(item.subitems ?? [], depth + 1)]);
}

export type PublicationSection = {
	linear: boolean;
	load: (request: (url: string) => Promise<unknown>) => Promise<Element | Document>;
	cfiFromElement: (element: Element) => string;
};

export async function headingToc(sections: PublicationSection[], request: (url: string) => Promise<unknown>): Promise<TocEntry[]> {
	const entries: TocEntry[] = [];
	for (const section of sections) {
		if (!section.linear) continue;
		const loaded = await section.load(request);
		const doc = loaded.ownerDocument ?? (loaded as Document);
		const scope = doc.querySelector('.main_text, main, article, [role="main"]') ?? doc.body;
		if (!scope) continue;
		for (const heading of scope.querySelectorAll('h1, h2, h3, h4, h5, h6')) {
			if (heading.closest('nav, header, footer, .metadata, .conversion, [hidden], [aria-hidden="true"]')) continue;
			const label = heading.textContent?.replace(/\s+/g, ' ').trim();
			if (!label) continue;
			entries.push({ href: section.cfiFromElement(heading), label, depth: 0 });
		}
	}
	return entries;
}

// epub.js uses the OPF page progression to arrange columns, and also injects
// direction:rtl on <html>. Keep that column order while preserving the original
// text direction in body blocks. Low specificity lets the publisher's own CSS
// and dir attributes continue to control individual blocks and mixed scripts.
export function preserveTextDirection(doc: Document, source?: Document) {
	const root = doc.documentElement;
	const injected = root.style.direction;
	root.style.direction = source?.documentElement.style.direction ?? '';
	const direction = doc.defaultView?.getComputedStyle(doc.body).direction ?? 'ltr';
	root.style.direction = injected;
	if (doc.getElementById('belib-text-direction')) return;
	const style = doc.createElement('style');
	style.id = 'belib-text-direction';
	style.textContent = `
		html { width: 100% !important; box-sizing: border-box; background-color: #fff !important; }
		body { box-sizing: border-box; margin: 0 !important; padding-left: 16px !important; padding-right: 16px !important; background-color: #fff !important; }
		:where(body > :not([dir])) { direction: ${direction === 'rtl' ? 'rtl' : 'ltr'}; }
	`;
	doc.head.append(style);
}
