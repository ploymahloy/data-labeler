import { useState } from "react";
import type { FormEvent } from "react";
import { Navigate } from "react-router-dom";
import type { Role } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { consumeReturnPath, peekReturnPath } from "../auth/returnTo";
import { config } from "../config";

const ALL_ROLES: Role[] = ["labeler", "reviewer", "admin"];

export const LoginPage = () => {
  const { identity, ready, loginDev, loginCognito } = useAuth();
  const [email, setEmail] = useState("");
  const [roles, setRoles] = useState<Role[]>(["labeler"]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Back to the page the user was on when their session ended (dev login; Cognito returns via
  // the callback page).
  const [returnTo] = useState(() => peekReturnPath());
  if (ready && identity) {
    consumeReturnPath();
    return <Navigate to={returnTo} replace />;
  }

  const toggle = (role: Role) =>
    setRoles((prev) => (prev.includes(role) ? prev.filter((r) => r !== role) : [...prev, role]));

  const onDevSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await loginDev(email.trim(), roles);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page page--narrow">
      <h1>Data Labeler</h1>
      {config.authMode === "cognito" ? (
        <>
          <p>Sign in with your organization account.</p>
          <button onClick={() => void loginCognito()}>Sign in</button>
        </>
      ) : (
        <form className="card" onSubmit={onDevSubmit}>
          <p className="warning-text">Local development login. Not available in production.</p>
          <label>
            Email
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
            />
          </label>
          <fieldset>
            <legend>Roles</legend>
            {ALL_ROLES.map((role) => (
              <label key={role} className="inline">
                <input type="checkbox" checked={roles.includes(role)} onChange={() => toggle(role)} /> {role}
              </label>
            ))}
          </fieldset>
          <button type="submit" disabled={busy || !email.trim()}>
            Sign in
          </button>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
        </form>
      )}
    </div>
  );
};
