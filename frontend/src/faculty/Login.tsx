import { useState } from "react";
import { post } from "../api";
import { Link } from "../ui";

export function Login({ onLogin }: { onLogin: (u: any) => void }) {
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      const r = await post("/api/auth/login", { email, password: pw });
      onLogin(r.user);
    } catch (ex: any) {
      setErr(ex.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="login">
      <form className="panel" onSubmit={submit}>
        <div className="wordmark">vaani<span style={{ color: "#c79a12" }}>.</span></div>
        <div>
          <h1 style={{ fontSize: 22 }}>Faculty sign in</h1>
          <p className="muted">Set up vivas, review recordings and confirm marks.</p>
        </div>
        <label className="field"><span>Email</span><input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" required /></label>
        <label className="field"><span>Password</span><input className="input" type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="current-password" required /></label>
        {err && <p className="error-text" role="alert">{err}</p>}
        <button className="btn" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
        <p className="muted" style={{ fontSize: 14 }}>Demo: faculty@vaani.local / faculty123</p>
        <Link to="/" className="muted">Taking a viva? Join as a student</Link>
      </form>
    </div>
  );
}
