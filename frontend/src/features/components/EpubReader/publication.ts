// epub.js injects the OPF page progression direction on <html>. Preserve that
// column order while keeping the publication's original body text direction.
export function preserveTextDirection(document: Document) {
	const root = document.documentElement;
	const injectedDirection = root.style.direction;
	root.style.direction = '';
	const direction = document.defaultView?.getComputedStyle(document.body).direction ?? 'ltr';
	root.style.direction = injectedDirection;
	if (document.getElementById('belib-text-direction')) return;
	const style = document.createElement('style');
	style.id = 'belib-text-direction';
	style.textContent = `:where(body > :not([dir])) { direction: ${direction === 'rtl' ? 'rtl' : 'ltr'}; }`;
	document.head.append(style);
}
