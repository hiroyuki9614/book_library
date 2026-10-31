import Rendition from 'epubjs/src/rendition';
import type Book from 'epubjs/types/book';
import type NativeRendition from 'epubjs/types/rendition';
import type { Location, RenditionOptions } from 'epubjs/types/rendition';
import type Contents from 'epubjs/types/contents';
import { PublicationManager } from './PublicationManager';

type Native = NativeRendition & {
	resize: (width: number, height: number, cfi?: string) => void;
	manager?: { settings?: { axis?: string; gap?: number } };
	settings: NativeRendition['settings'] & { gap?: number };
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
	let effectiveDirection: 'ltr' | 'rtl' = pageTurnDirection ?? 'ltr';
	// rendition.start() otherwise always reapplies OPF metadata. Apply the Book
	// setting after start but before the first display so edge semantics and the
	// native horizontal spread use the same authoritative direction.
	const directionReady = primary.started.then(() => {
		const publicationDirection = (book as unknown as { package: { metadata: { direction?: string } } }).package.metadata.direction;
		effectiveDirection = pageTurnDirection ?? (publicationDirection === 'rtl' ? 'rtl' : 'ltr');
		primary.direction(effectiveDirection);
	});
	let preview: Native | undefined;
	let contentHook: ((contents: Contents) => unknown) | undefined;
	let keyHook: ((event: KeyboardEvent) => void) | undefined;
	let mode = options.spread ?? 'auto';
	let customDual = false;
	let nativeDual = false;
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
		// epub.js writes half of the column gap as body padding. A fixed 32px gap
		// gives the full-width publication a 16px text inset at every viewport.
		const gap = 32;
		primary.settings.gap = gap;
		if (primary.manager?.settings) primary.manager.settings.gap = gap;
		const requestedDual = mode === 'always' || (mode === 'auto' && viewer.clientWidth >= 800);
		// epub.js's native spread arithmetic is reliable for horizontal sections.
		// Vertical writing uses two real single-page renditions because the native
		// spread manager advances on the wrong axis. Mixed books switch per section.
		const nextNativeDual = requestedDual && primary.manager?.settings?.axis === 'horizontal';
		const nextCustomDual = requestedDual && !nextNativeDual;
		// epub.js remeasures custom panes with integer clientWidth. A half-pixel
		// request would differ on every preview.resize(), destroying/reloading the
		// same spine at odd viewport widths. Native spreads keep the full width.
		const width = nextCustomDual ? Math.floor(viewer.clientWidth / 2) : viewer.clientWidth;
		const farSide = viewer.clientWidth - width;
		const height = viewer.clientHeight;
		const rtl = effectiveDirection === 'rtl';
		const changed = customDual !== nextCustomDual || nativeDual !== nextNativeDual || Number(primaryHost.dataset.width) !== width || Number(primaryHost.dataset.height) !== height;
		customDual = nextCustomDual;
		nativeDual = nextNativeDual;
		primaryHost.style.width = `${width}px`;
		primaryHost.style.left = customDual && rtl ? `${farSide}px` : '0';
		previewHost.style.width = `${width}px`;
		previewHost.style.left = rtl ? '0' : `${farSide}px`;
		previewHost.style.display = customDual ? 'block' : 'none';
		previewHost.style.visibility = 'hidden';
		if (changed) {
			const cfi = anchor || locationOf(primary)?.start?.cfi;
			primaryHost.dataset.width = String(width);
			primaryHost.dataset.height = String(height);
			primary.spread(nativeDual ? 'always' : 'none', nativeDual ? 1 : 800);
			primary.resize(width, height, cfi);
			await frames();
		}
		current = locationOf(primary);
		if (customDual && current?.start && !current.atEnd) {
			if (!preview) {
				preview = new Rendition(book, { ...options, manager: PublicationManager, width, height, spread: 'none' }) as Native;
				await preview.started;
				preview.direction(effectiveDirection);
				if (contentHook) preview.hooks.content.register(contentHook);
				preview.hooks.content.register(routeLinks);
				preview.on('keydown', (event: KeyboardEvent) => keyHook?.(event));
				preview.on('removed', (section: unknown, view: unknown) => primary.emit('removed', section, view));
				await preview.attachTo(previewHost);
			}
			preview.settings.gap = gap;
			if (preview.manager?.settings) preview.manager.settings.gap = gap;
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
		await directionReady;
		if (typeof target === 'number') await primary.display(target);
		else await primary.display(target);
	}, typeof target === 'string' && target.startsWith('epubcfi(') ? target : undefined);
	primary.on('relocated', (location: Location) => {
		if (!busy && !closed) {
			if (customDual || nativeDual) void run(async () => {}).catch(failed);
			else { current = location; publish(); }
		}
	});
	const overrides = {
		display,
		next: () => run(async () => {
			if (current?.atEnd) return;
			await primary.next(); await frames();
			if (customDual && !locationOf(primary)?.atEnd) await primary.next();
		}),
		prev: () => run(async () => {
			if (current?.atStart) return;
			await primary.prev(); await frames();
			if (customDual && !locationOf(primary)?.atStart) await primary.prev();
		}),
		spread: (value: string) => run(async () => {
			mode = value;
		}),
		resize: (_width: number, _height: number, cfi?: string) => run(async () => {}, cfi),
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
