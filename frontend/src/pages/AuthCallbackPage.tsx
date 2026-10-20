import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

/** Landing page for the Cognito Hosted UI redirect (authorization code + PKCE). */
export const AuthCallbackPage = () => {
  const { completeCognitoLogin } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return; // the code can only be exchanged once
    started.current = true;
    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");
    const state = params.get("state");
    if (!code || !state) {
      setError(params.get("error_description") ?? "Missing authorization code");
      return;
    }
    completeCognitoLogin(code, state)
      .then(() => navigate("/", { replace: true }))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Sign-in failed"));
  }, [completeCognitoLogin, navigate]);

  if (error) {
    return (
      <div className="page page--narrow error" role="alert">
        <p>{error}</p>
        <Link to="/login">Back to sign in</Link>
      </div>
    );
  }
  return <div className="page page--narrow">Signing you in…</div>;
};
