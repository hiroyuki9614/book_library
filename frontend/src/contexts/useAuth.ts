import { useContext } from "react";
import { authContext } from "./authContext";

// 認証情報を提供するカスタムフック
export function useAuth() {
  const context = useContext(authContext);

  if (!context) {
    throw new Error("useAuth must be used within AuthProvider");
  }

  return context;
}
