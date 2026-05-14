// localStorageを使用してロールを永続化する仮実装のAuthProvider
// バックエンド実装時には cookieやJWTなどを使用して認証情報を管理する

import { useState } from "react";
import type { ReactNode } from "react";
import { authContext } from "./authContext";
import type { Role } from "./authContext";

interface Props {
  children: ReactNode;
}

export function AuthProvider({ children }: Props) {
  // const [role, setRole] = useState<Role>(null);

  // 仮実装：ロールをローカルストレージに保存して永続化
  const [role, setRole] = useState<Role>(() => {
    return localStorage.getItem("role") as Role;
  });

  const login = (role: Exclude<Role, null>) => {
    // 仮実装：ロールをローカルストレージに保存して永続化
    localStorage.setItem("role", role);
    setRole(role);
  };

  const logout = () => {
    // 仮実装：ロールをローカルストレージから削除して永続化
    localStorage.removeItem("role");
    setRole(null);
  };

  return <authContext.Provider value={{ role, login, logout }}>{children}</authContext.Provider>;
}
