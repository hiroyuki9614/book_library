import { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { Navigate, useOutlet } from 'react-router-dom';
import { useAuth } from '@/contexts/useAuth';
import useRefreshAuthOnFocus from '@/hooks/useRefreshAuthOnFocus';

const RequireAuth = () => {
	const { isAuthenticated, isPending, user } = useAuth();
	const refreshState = useRefreshAuthOnFocus(isAuthenticated);
	const outlet = useOutlet();
	const statusRef = useRef<HTMLDivElement>(null);
	const focusBeforeCheck = useRef<{ element: HTMLElement; userId: number } | null>(null);
	const currentUserId = useRef(user?.id);
	useEffect(() => {
		currentUserId.current = user?.id;
	}, [user?.id]);
	const isIdentityUncertain = isPending
		|| refreshState.isRefreshing
		|| (isAuthenticated && !refreshState.hasValidated);

	useEffect(() => {
		if (!isIdentityUncertain) return;

		const activeElement = document.activeElement;
		if (activeElement instanceof HTMLElement && activeElement !== document.body && currentUserId.current !== undefined) {
			focusBeforeCheck.current = { element: activeElement, userId: currentUserId.current };
		}

		const portals = new Map<HTMLElement, { ariaHidden: string | null; inert: boolean; visibility: string }>();
		const concealPortal = (element: Element) => {
			if (!(element instanceof HTMLElement) || portals.has(element)) return;
			portals.set(element, {
				ariaHidden: element.getAttribute('aria-hidden'),
				inert: element.inert,
				visibility: element.style.visibility,
			});
			element.setAttribute('aria-hidden', 'true');
			element.inert = true;
			element.style.visibility = 'hidden';
		};
		const concealSheetPortals = (node: Node) => {
			if (!(node instanceof Element)) return;
			if (node.matches('[data-slot="sheet-content"]')) concealPortal(node);
			node.querySelectorAll('[data-slot="sheet-content"]').forEach(concealPortal);
			if (node.contains(document.activeElement)) focusStatus();
		};
		const focusStatus = () => {
			if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
			statusRef.current?.removeAttribute('aria-hidden');
			statusRef.current?.focus();
		};

		document.querySelectorAll('[data-slot="sheet-content"]').forEach(concealPortal);
		focusStatus();
		const observer = new MutationObserver((records) => {
			for (const record of records) record.addedNodes.forEach(concealSheetPortals);
		});
		observer.observe(document.body, { childList: true, subtree: true });

		const blockReaderNavigation = (event: KeyboardEvent) => {
			if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return;
			event.preventDefault();
			event.stopImmediatePropagation();
		};
		document.addEventListener('keydown', blockReaderNavigation, true);

		return () => {
			observer.disconnect();
			document.removeEventListener('keydown', blockReaderNavigation, true);
			for (const [element, previous] of portals) {
				if (previous.ariaHidden === null) element.removeAttribute('aria-hidden');
				else element.setAttribute('aria-hidden', previous.ariaHidden);
				element.inert = previous.inert;
				element.style.visibility = previous.visibility;
			}
		};
	}, [isIdentityUncertain]);

	useEffect(() => {
		if (isIdentityUncertain) return;
		const previousFocus = focusBeforeCheck.current;
		focusBeforeCheck.current = null;
		if (previousFocus && previousFocus.userId === user?.id && previousFocus.element.isConnected) {
			const element = previousFocus.element;
			requestAnimationFrame(() => element.focus());
		}
	}, [isIdentityUncertain, user?.id]);

	if (!isAuthenticated) {
		if (isPending) return <div>Loading...</div>;
		return <Navigate to='/login' replace />;
	}

	return (
		<div className='relative min-h-screen'>
			<div
				aria-hidden={isIdentityUncertain ? 'true' : undefined}
				inert={isIdentityUncertain}
				style={isIdentityUncertain ? { visibility: 'hidden' } : undefined}
			>
				{outlet}
			</div>
			{isIdentityUncertain && createPortal(
				<div ref={statusRef} role='status' aria-live='polite' tabIndex={-1} className='fixed inset-0 z-[100] grid place-items-center bg-background text-foreground'>
					セッションを確認しています。
				</div>,
				document.body,
			)}
		</div>
	);
};

export default RequireAuth;
