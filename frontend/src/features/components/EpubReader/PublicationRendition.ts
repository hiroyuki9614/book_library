import Rendition from 'epubjs/src/rendition';
import type Book from 'epubjs/types/book';
import type NativeRendition from 'epubjs/types/rendition';
import type { Location, RenditionOptions } from 'epubjs/types/rendition';
import type Contents from 'epubjs/types/contents';
import { PublicationManager } from './PublicationManager';

type Native = NativeRendition & {
	resize: (width: number, height: number, cfi?: string) => void;
};
export type PublicationRendition = Native & {
	previewHooks: (content: (contents: Contents) => unknown, key: (event: KeyboardEvent) => void) => void;
	disposePreview: () => Promise<void>;
};
const frames = async () => {
	await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
	await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
};
const locationOf = (renderer: Native) => renderer.currentLocation() as unknown as Location;

// epub.js supports vertical single pages, but its native spread arithmetic is
// horizontal-only. Spreads use two actual single-page renditions:
// the primary (saved CFI) page and the following page, in publication read order.
// Both retain the publisher's vertical CSS, ruby and original source document.
export function createPublicationRendition(book: Book, viewer: HTMLElement, options: RenditionOptions, pageTurnDirection?: 'ltr' | 'rtl'): PublicationRendition {
	const primaryHost = document.createElement('div');
	const previewHost = document.createElement('div');
	Object.assign(viewer.style, { position: 'relative' });
	for (const host of [primaryHost, previewHost]) Object.assign(host.style, { position: 'absolute', top: '0', height: '100%' });
	primaryHost.style.width = '100%';
	previewHost.style.display = 'none';
	primaryHost.dataset.publicationPane = 'primary';
	previewHost.dataset.publicationPane = 'following';
	viewer.append(primaryHost, previewHost);
	const primary = book.renderTo(primaryHost, { ...options, spread: 'none', manager: PublicationManager }) as Native;
	let preview: Native | undefined;
	let contentHook: ((contents: Contents) => unknown) | undefined;
	let keyHook: ((event: KeyboardEvent) => void) | undefined;
	let mode = options.spread ?? 'auto';
	let dual = false;
	let busy = false;
	let closed = false;
	let pending: Promise<unknown> = Promise.resolve();
	let pendingCount = 0;
	let current: Location | undefined;
	const subscribers = new Set<(location: Location) => void>();
	const failed = (error: unknown) => primary.emit('reader-error', error);
	const routeLinks = (contents: Contents) => {
		contents.document.addEventListener('click', (event) => {
			const anchor = (event.target as Element)?.closest?.('a[href]');
			const href = anchor?.getAttribute('href');
			if (!href || /^(?:[a-z][a-z0-9+.-]*:|\/\/)/i.test(href)) return;
			event.preventDefault(); event.stopImmediatePropagation();
			const section = book.spine.get(contents.sectionIndex);
			const target = new URL(href, new URL(section.href, 'https://publication.invalid/'));
			void display(target.pathname.slice(1) + target.hash).catch(failed);
		}, true);
	};
	primary.hooks.content.register(routeLinks);
	const publish = () => {
		if (!closed && current?.start) for (const listener of subscribers) listener(current);
	};
	const sync = async (anchor?: string) => {
		// Keep the same two-pane geometry across mixed horizontal/vertical spine
		// sections so crossing a chapter cannot reflow past unseen source text.
		const nextDual = mode === 'always' || (mode === 'auto' && viewer.clientWidth >= 800);
		const width = viewer.clientWidth / (nextDual ? 2 : 1);
		const height = viewer.clientHeight;
		const rtl = pageTurnDirection ? pageTurnDirection === 'rtl' : (book as unknown as { package: { metadata: { direction?: string } } }).package.metadata.direction === 'rtl';
		const changed = dual !== nextDual || Number(primaryHost.dataset.width) !== width || Number(primaryHost.dataset.height) !== height;
		dual = nextDual;
		primaryHost.style.width = `${width}px`;
		primaryHost.style.left = dual && rtl ? `${width}px` : '0';
		previewHost.style.width = `${width}px`;
		previewHost.style.left = rtl ? '0' : `${width}px`;
		previewHost.style.display = dual ? 'block' : 'none';
		previewHost.style.visibility = 'hidden';
		if (changed) {
			const cfi = anchor || locationOf(primary)?.start?.cfi;
			primaryHost.dataset.width = String(width);
			primaryHost.dataset.height = String(height);
			primary.spread('none');
			primary.resize(width, height, cfi);
			await frames();
			await primary.display(cfi);
			await frames();
		}
		current = locationOf(primary);
		if (dual && current?.start && !current.atEnd) {
			if (!preview) {
				preview = new Rendition(book, { ...options, manager: PublicationManager, width, height, spread: 'none' }) as Native;
				if (contentHook) preview.hooks.content.register(contentHook);
				preview.hooks.content.register(routeLinks);
				preview.on('keydown', (event: KeyboardEvent) => keyHook?.(event));
				preview.on('removed', (section: unknown, view: unknown) => primary.emit('removed', section, view));
				await preview.attachTo(previewHost);
			}
			preview.resize(width, height);
			await frames();
			await preview.display(current.start.cfi);
			await frames();
			await preview.next();
			await frames();
			const following = locationOf(preview);
			if (following?.start?.cfi !== current.start.cfi) {
				previewHost.style.visibility = 'visible';
				current = { ...current, end: following.end, atEnd: following.atEnd };
			}
		}
	};
	const run = (action: () => Promise<unknown>, anchor?: string) => {
		if (closed) return Promise.resolve();
		pendingCount++;
		primary.emit('reader-busy', true);
		const result = pending.catch(() => undefined).then(async () => {
			if (closed) return;
			busy = true;
			try {
				await action();
				await frames();
				await sync(anchor);
				publish();
			} finally {
				busy = false;
			}
		}).finally(() => {
			pendingCount--;
			if (!pendingCount) primary.emit('reader-busy', false);
		});
		pending = result;
		return result as Promise<void>;
	};
	const display = (target?: string | number) => run(async () => {
		if (typeof target === 'number') await primary.display(target);
		else await primary.display(target);
	}, typeof target === 'string' && target.startsWith('epubcfi(') ? target : undefined);
	primary.on('relocated', (location: Location) => {
		if (!busy && !closed) {
			if (dual) void run(async () => {}).catch(failed);
			else { current = location; publish(); }
		}
	});
	const overrides = {
		display,
		next: () => run(async () => {
			if (current?.atEnd) return;
			await primary.next(); await frames();
			if (dual && !locationOf(primary)?.atEnd) await primary.next();
		}),
		prev: () => run(async () => {
			if (current?.atStart) return;
			await primary.prev(); await frames();
			if (dual && !locationOf(primary)?.atStart) await primary.prev();
		}),
		spread: (value: string) => run(async () => {
			mode = value;
		}),
		resize: (_width: number, _height: number, cfi?: string) => run(async () => { await sync(cfi); }),
		previewHooks: (content: typeof contentHook, key: typeof keyHook) => { contentHook = content; keyHook = key; },
		disposePreview: async () => {
			closed = true;
			await pending.catch(() => undefined);
			preview?.destroy();
			primaryHost.remove();
			previewHost.remove();
		},
		on: (event: string, listener: (location: Location) => void) => event === 'relocated' ? subscribers.add(listener) : primary.on(event, listener),
		off: (event: string, listener: (location: Location) => void) => event === 'relocated' ? subscribers.delete(listener) : primary.off(event, listener),
	};
	return new Proxy(primary, {
		get(target, key) {
			if (key in overrides) return overrides[key as keyof typeof overrides];
			const value = Reflect.get(target, key);
			return typeof value === 'function' ? value.bind(target) : value;
		},
	}) as PublicationRendition;
}
