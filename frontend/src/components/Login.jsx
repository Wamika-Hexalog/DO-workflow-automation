import { useState } from "react";
import mark from "../assets/hexalog-mark.svg";
import { api } from "../api.js";

export default function Login({ onSignedIn }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      const me = await api.login(form.get("username"), form.get("password"));
      onSignedIn(me.username);
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  return (
    <main className="login">
      <form className="login-card" onSubmit={submit}>
        <img src={mark} alt="Hexalog" width="28" height="32" />
        <h1>Sign in to DO Workflow</h1>
        <div className="field">
          <label htmlFor="login-username">Username or email</label>
          <input id="login-username" name="username" className="input" autoComplete="username" required autoFocus />
        </div>
        <div className="field">
          <label htmlFor="login-password">Password</label>
          <input id="login-password" name="password" type="password" className="input" autoComplete="current-password" required />
        </div>
        {error && (
          <p className="login-error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="btn btn-primary" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
