import { useState } from "react";
import { post } from "../api";
import { Link, navigate } from "../ui";

export function Join() {
  const params = new URLSearchParams(location.search);
  const [code, setCode] = useState(params.get("code") || "");
  const [roll, setRoll] = useState("");
  const [name, setName] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      await post("/api/auth/join", { code: code.trim(), roll_no: roll.trim(), name: name.trim() });
      navigate("/viva");
    } catch (ex: any) {
      setErr(ex.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="board">
      <div className="join">
        <section className="join-left">
          <div className="wordmark">vaani<span className="dot">.</span></div>
          <div>
            <h1>Your viva, <em>spoken</em> and fair.</h1>
            <p className="lede">A fifteen-minute oral exam with an AI examiner that listens, asks follow-up questions and never
              hints at the answers. Your faculty reviews every mark.</p>
          </div>
          <ol className="chalk-steps">
            <li><span><b>Join</b> with the code your faculty shared and your roll number.</span></li>
            <li><span><b>Check</b> your microphone and speakers, and read the rules.</span></li>
            <li><span><b>Talk</b> it through. Say "I don't know" or "please repeat" whenever you need to.</span></li>
          </ol>
        </section>
        <section className="join-right">
          <form onSubmit={submit} aria-label="Join a viva">
            <h2>Join your viva</h2>
            <label className="field">
              <span>Viva code</span>
              <input className="input code-input" value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} maxLength={8}
                autoComplete="off" placeholder="DBMS26" required aria-describedby="code-help" />
              <small id="code-help">Six letters and digits, from your faculty.</small>
            </label>
            <label className="field">
              <span>Roll number</span>
              <input className="input" value={roll} onChange={(e) => setRoll(e.target.value)} placeholder="22CS1047" required autoComplete="off" />
            </label>
            <label className="field">
              <span>Your name</span>
              <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="As on your ID card" autoComplete="name" />
            </label>
            {err && <p className="banner bad" role="alert">{err}</p>}
            <button className="btn" disabled={busy || !code || !roll}>{busy ? "Joining…" : "Continue"}</button>
          </form>
          <Link to="/login" className="staff-link">Faculty sign in</Link>
        </section>
      </div>
    </div>
  );
}
