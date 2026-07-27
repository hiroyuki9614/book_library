// localStorageを使用してロールを永続化する仮実装のAuthProvider
// バックエンド実装時には cookieやJWTなどを使用して認証情報を管理する

import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { authContext } from './authContext';
import type { UserShape, Role } from './authContext';
import { authClient } from '@/lib/auth-client';
import { useNavigate } from 'react-router-dom';

interface Props {
  children: ReactNode;
}

export function AuthProvider({ children }: Props) {
  const [session, setSession] = useState<unknown | null>(null);
  const [user, setUser] = useState<UserShape | null>(null);
  const [isPending, setIsPending] = useState(true);

  useEffect(() => {
    let mounted = true;

    async function load() {
      setIsPending(true);
      const s = await authClient.getSession();
      if (!mounted) return;
      setSession(s ?? null);
      // s may be unknown — guard access
      setUser((s as unknown as { user?: UserShape })?.user ?? null);
      setIsPending(false);
    }

    load();

    return () => {
      mounted = false;
    };
  }, []);

  const navigate = useNavigate();

  const signOut = async () => {
    try {
      await authClient.signOut();
    } catch (err) {
      void err;
    }

    setSession(null);
    setUser(null);
    // Navigate without full page reload
    navigate('/login');
  };

  const isAuthenticated = Boolean(user);
  const role: Role = (user?.role as Role) ?? null;

  // Provide logout alias for backward compatibility
  const logout = signOut;

  return (
    <authContext.Provider value={{ session, user, isPending, isAuthenticated, role, signOut, logout }}>
      {children}
    </authContext.Provider>
  );
}
