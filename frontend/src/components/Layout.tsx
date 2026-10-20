import { Link, Navigate, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

/** Authenticated shell: header with identity + sign-out, and a guard that redirects to login. */
export const Layout = () => {
  const { identity, ready, logout } = useAuth();
  if (!ready) return <div className="page">Loading…</div>;
  if (!identity) return <Navigate to="/login" replace />;

  return (
    <>
      <header className="header">
        <Link to="/" className="header__brand">
          Data Labeler
        </Link>
        <span className="header__user">
          {identity.email || identity.sub} <span className="muted">({identity.roles.join(", ") || "no roles"})</span>
          <button className="secondary" onClick={logout}>
            Sign out
          </button>
        </span>
      </header>
      {identity.roles.length === 0 && (
        <div className="card warning" role="alert">
          Your account has no role yet. Ask an administrator to add you to the labeler or reviewer group.
        </div>
      )}
      <main>
        <Outlet />
      </main>
    </>
  );
};
