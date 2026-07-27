import type { AuthenticatedUser } from '@/contexts/authContext';
import { apiFetch } from '@/lib/api-client';

export function getCurrentUser(): Promise<AuthenticatedUser> {
	return apiFetch<AuthenticatedUser>('/api/v1/me');
}
