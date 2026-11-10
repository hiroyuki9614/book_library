import { afterEach, describe, expect, test, vi } from 'vitest';
import { Contents } from 'epubjs';
import IframeView from 'epubjs/src/managers/views/iframe';
import { PublicationView } from './PublicationView';
import { preserveTextDirection } from './publication';

afterEach(() => {
	vi.restoreAllMocks();
	document.querySelectorAll('iframe').forEach(frame => frame.remove());
});

describe('publication column direction', () => {
	for (const direction of ['ltr', 'rtl'] as const) {
		test(`${direction} controls real horizontal columns across reflow, preserving source bidi and nodes`, async () => {
			const frame = document.createElement('iframe');
			document.body.append(frame);
			const doc = frame.contentDocument!;
			doc.head.innerHTML = `<style>html{direction:${direction === 'ltr' ? 'rtl' : 'ltr'}!important}body{writing-mode:horizontal-tb;direction:rtl!important}p{margin:0;break-after:column}</style>`;
			doc.body.innerHTML = '<p>First العربية</p><p dir="ltr">Second</p>';
			const nodes = [...doc.body.childNodes];
			const contents = new Contents(doc, doc.body, '/6/2', 0);
			vi.spyOn(IframeView.prototype, 'load').mockResolvedValue(contents);
			// Isolate iframe loading; columns and Chromium layout are real.
			const view = Object.create(PublicationView.prototype) as PublicationView;
			view.settings = { direction };
			await view.load('');
			for (const width of [400, 320]) {
				contents.columns(width, 300, width / 2 - 16, 32, direction === 'ltr' ? 'rtl' : 'ltr');
				const [first, second] = [...doc.querySelectorAll('p')];
				const style = frame.contentWindow!.getComputedStyle.bind(frame.contentWindow);
				expect(style(doc.documentElement).direction).toBe(direction);
				expect(doc.documentElement.style.getPropertyPriority('direction')).toBe('important');
				expect(style(doc.body).direction).toBe(direction);
				expect(style(first).direction).toBe('rtl');
				expect(style(second).direction).toBe('ltr');
				if (direction === 'ltr') expect(first.getBoundingClientRect().left).toBeLessThan(second.getBoundingClientRect().left);
				else expect(first.getBoundingClientRect().left).toBeGreaterThan(second.getBoundingClientRect().left);
				nodes.forEach((node, index) => expect(doc.body.childNodes[index]).toBe(node));
			}
			contents.destroy();
		});

		test(`${direction} root retains inline priority while capturing source inline important bidi`, () => {
			const frame = document.createElement('iframe');
			document.body.append(frame);
			const doc = frame.contentDocument!;
			const sourceDirection = direction === 'ltr' ? 'rtl' : 'ltr';
			doc.head.innerHTML = `<style>html{direction:${direction}!important}</style>`;
			doc.body.innerHTML = '<p>Source العربية</p><p dir="ltr">Explicit LTR</p>';
			doc.documentElement.style.setProperty('direction', direction, 'important');
			const source = document.implementation.createHTMLDocument();
			source.documentElement.style.setProperty('direction', sourceDirection, 'important');
			for (let hook = 0; hook < 2; hook++) {
				preserveTextDirection(doc, source);
				expect(doc.documentElement.style.direction).toBe(direction);
				expect(doc.documentElement.style.getPropertyPriority('direction')).toBe('important');
				expect(frame.contentWindow!.getComputedStyle(doc.querySelector('p')!).direction).toBe(sourceDirection);
				expect(frame.contentWindow!.getComputedStyle(doc.querySelector('[dir]')!).direction).toBe('ltr');
				expect(source.documentElement.style.getPropertyPriority('direction')).toBe('important');
				expect(doc.querySelectorAll('#belib-text-direction')).toHaveLength(1);
			}
		});
	}

	test('vertical publication keeps native columns and its source direction', async () => {
		const frame = document.createElement('iframe');
		document.body.append(frame);
		const doc = frame.contentDocument!;
		doc.body.style.cssText = 'writing-mode:vertical-rl;direction:rtl';
		const contents = new Contents(doc, doc.body, '/6/2', 0);
		const columns = contents.columns;
		vi.spyOn(IframeView.prototype, 'load').mockResolvedValue(contents);
		const view = Object.create(PublicationView.prototype) as PublicationView;
		view.settings = { direction: 'ltr' };
		await view.load('');
		expect(contents.columns).toBe(columns);
		expect(doc.body.style.direction).toBe('rtl');
		expect(doc.documentElement.style.writingMode).toBe('vertical-rl');
		contents.destroy();
	});
});
