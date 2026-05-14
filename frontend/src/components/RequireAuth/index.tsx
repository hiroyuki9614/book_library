import { useAuth } from "@/contexts/useAuth";
import { Navigate, Outlet } from "react-router-dom";

const RequireAuth = () => {
  const { role } = useAuth();
  if (!role) {
    // 認証されていなければログインページへリダイレクト
    return <Navigate to="/login" replace />;
  }
  return <Outlet />;
};

export default RequireAuth;
