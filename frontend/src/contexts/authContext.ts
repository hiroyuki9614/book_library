import { createContext } from "react";

export type Role = "admin" | "user" | null;

export interface AuthContextType {
  role: Role;
  login: (role: Exclude<Role, null>) => void;
  logout: () => void;
}

export const authContext = createContext<AuthContextType | undefined>(undefined);
