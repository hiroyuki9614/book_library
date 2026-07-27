import { createContext } from "react";

export type Role = 'admin' | 'user' | null;

export interface UserShape {
  id: number | string;
  email?: string;
  role?: Role;
  [k: string]: unknown;
}

export interface AuthContextType {
  session: unknown | null;
  user: UserShape | null;
  isPending: boolean;
  isAuthenticated: boolean;
  role: Role;
  signOut: () => Promise<void> | void;
  // 互換性: 既存コードが logout を参照しているため alias を追加
  logout?: () => Promise<void> | void;
}

export const authContext = createContext<AuthContextType | undefined>(undefined);
