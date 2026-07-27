import { createContext } from 'react';

export type Role = 'admin' | 'user' | null;

export interface AuthenticatedUser {
	id: number;
	name: string;
	email: string;
	role: Exclude<Role, null>;
}

export interface AuthContextType {
	user: AuthenticatedUser | null;
	isPending: boolean;
	isAuthenticated: boolean;
	role: Role;
	refreshAuth: () => Promise<AuthenticatedUser | null>;
	signOut: () => Promise<void>;
	logout: () => Promise<void>;
}

export const authContext = createContext<AuthContextType | undefined>(undefined);
