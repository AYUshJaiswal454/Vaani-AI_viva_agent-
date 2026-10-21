import { useEffect, useState } from "react";
import { get, post } from "../api";
import { DIM_NAMES, DIMS, Empty, navigate, Spinner, statusPill, useAsync } from "../ui";

export function Vivas() {
  const { data, error, loading } = useAsync(() => get("/api/vivas"), []);
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Vivas</h1>
          <p className="sub">Share a viva's code with your class. Every attempt is recorded, scored and waits for your review.</p>
        </div>
        <button className="btn" onClick={() => navigate("/faculty/vivas/new")}>New viva</button>
      </div>
      {loading && <Spinner label="Loading vivas" />}
      {error && <p className="error-text">{error}</p>}
      {data && !data.vivas.length && <div className="panel"><Empty><p>No vivas yet.</p><button className="btn" onClick={() => navigate("/faculty/vivas/new")}>Create your first viva</button></Empty></div>}
      <div className="vivas">
        {data?.vivas.map((v: any) => (
          <div key={v.id} className="panel viva-row" role="link" tabIndex={0} onClick={() => navigate(`/faculty/vivas/${v.id}`)}
            onKeyDown={(e) => e.key === "Enter" && navigate(`/faculty/vivas/${v.id}`)}>
            <div>
              <div className="t">{v.title}</div>
              <div className="s">{v.subject}, from “{v.bank.name}”</div>
            </div>
            <span className="code-chip" title="Students join with this code">{v.access_code}</span>
            <div className="stat"><b>{v.attempts}</b><span>attempts</span></div>
            <div className="stat"><b className={v.needs_review ? "" : "muted"}>{v.needs_review}</b><span>to review</span></div>
            <div className="stat"><b>{v.average ?? "–"}</b><span>average</span></div>
            {statusPill(v.status)}
          </div>
        ))}
      </div>
    </>
  );
}

export function NewViva() {
  const banks = useAsync(() => get("/api/banks"), []);
  const defaults = useAsync(() => get("/api/vivas"), []);
  const sys = useAsync(() => get("/api/system/status"), []);
  const [f, setF] = useState<any>({ title: "", bank_id: "", difficulty: "mixed", language: "en", stt_engine: "", tts_engine: "", live_llm: true,
    integrity: true, code_round: true, allow_end_early: true, barge_in: true, roster: "", topics: [] as string[], custom: false, justification: "" });
  const [weights, setWeights] = useState<Record<string, number>>({ conceptual: 30, depth: 25, problem_solving: 20, practical: 15, communication: 10 });
  const [phases, setPhases] = useState<any[]>([]);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (defaults.data) setPhases(defaults.data.defaults.phases.map((p: any) => ({ ...p }))); }, [defaults.data]);
  const bank = banks.data?.banks.find((b: any) => String(b.id) === String(f.bank_id));
  const total = DIMS.reduce((s, k) => s + (+weights[k] || 0), 0);
  const set = (k: string, v: any) => setF((x: any) => ({ ...x, [k]: v }));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      const rubric = { dimensions: DIMS.map((id) => ({ id, weight: +weights[id] })) };
      const body: any = {
        bank_id: +f.bank_id, title: f.title || `${bank?.subject} viva`, difficulty: f.difficulty, topics: f.topics,
        settings: { language: f.language, stt_engine: f.stt_engine || null, tts_engine: f.tts_engine || null, live_llm: f.live_llm,
          integrity: f.integrity, code_round: f.code_round, allow_end_early: f.allow_end_early, barge_in: f.barge_in },
        roster: f.roster.split("\n").map((l: string) => l.trim()).filter(Boolean), rubric,
      };
      if (f.custom) body.phase_plan = { phases: phases.map((p) => ({ id: p.id, start: +p.start, end: +p.end })), justification: f.justification };
      const r = await post("/api/vivas", body);
      navigate(`/faculty/vivas/${r.viva.id}`);
    } catch (ex: any) {
      setErr(ex.message);
    } finally {
      setBusy(false);
    }
  }

  const sttOptions = sys.data ? ["whisper-onnx", "faster-whisper", "groq", "openai", "sarvam", "browser"].filter((k) => sys.data.stt[k]?.available) : [];
  const ttsOptions = sys.data ? ["browser", "openai", "sarvam", "piper", "flite"].filter((k) => sys.data.tts[k]?.available) : [];

  return (
    <form onSubmit={submit} className="stack" style={{ maxWidth: 880 }}>
      <div className="page-head"><div><h1>New viva</h1><p className="sub">Choose an approved question bank; everything else has sensible defaults.</p></div></div>
      <div className="panel panel-pad stack">
        <h2>Basics</h2>
        <div className="grid-2">
          <label className="field"><span>Question bank</span>
            <select className="input" value={f.bank_id} onChange={(e) => set("bank_id", e.target.value)} required>
              <option value="">Choose…</option>
              {banks.data?.banks.map((b: any) => <option key={b.id} value={b.id} disabled={!b.ready}>{b.name} ({b.approved} approved){b.ready ? "" : " — not ready"}</option>)}
            </select>
          </label>
          <label className="field"><span>Title</span><input className="input" value={f.title} onChange={(e) => set("title", e.target.value)} placeholder={bank ? `${bank.subject} viva` : ""} /></label>
          <label className="field"><span>Difficulty</span>
            <select className="input" value={f.difficulty} onChange={(e) => set("difficulty", e.target.value)}>
              <option value="mixed">Adaptive (starts easy, adjusts to the student)</option><option value="easy">Easy</option><option value="medium">Medium</option><option value="hard">Hard</option>
            </select>
          </label>
          <label className="field"><span>Answer language</span>
            <select className="input" value={f.language} onChange={(e) => set("language", e.target.value)}>
              <option value="en">English</option><option value="hinglish">English or Hinglish</option>
            </select>
            <small>Accent and language mix never affect content marks.</small>
          </label>
        </div>
        {bank && Object.keys(bank.topics || {}).length > 0 && (
          <fieldset className="field" style={{ border: "none", padding: 0 }}>
            <span>Topics (leave all unticked to use the whole bank)</span>
            <div className="row">{Object.keys(bank.topics).map((t) => (
              <label key={t} className="pill" style={{ cursor: "pointer" }}>
                <input type="checkbox" checked={f.topics.includes(t)} onChange={(e) => set("topics", e.target.checked ? [...f.topics, t] : f.topics.filter((x: string) => x !== t))} /> {t}
              </label>))}
            </div>
          </fieldset>
        )}
      </div>
      <div className="panel panel-pad stack">
        <h2>Voice</h2>
        <div className="grid-2">
          <label className="field"><span>Speech recognition</span>
            <select className="input" value={f.stt_engine} onChange={(e) => set("stt_engine", e.target.value)}>
              <option value="">Server default ({sys.data?.stt?.default || "…"})</option>
              {sttOptions.map((k) => <option key={k} value={k}>{k}</option>)}
            </select>
          </label>
          <label className="field"><span>Examiner voice</span>
            <select className="input" value={f.tts_engine} onChange={(e) => set("tts_engine", e.target.value)}>
              <option value="">Server default ({sys.data?.tts?.default || "…"})</option>
              {ttsOptions.map((k) => <option key={k} value={k}>{k}</option>)}
            </select>
          </label>
        </div>
        <div className="row">
          {[["live_llm", "AI-phrased follow-ups (needs an AI provider)"], ["barge_in", "Students may interrupt the examiner"], ["code_round", "Show code and a scratchpad for applied problems"],
            ["integrity", "Record integrity signals"], ["allow_end_early", "Allow ending early"]].map(([k, label]) => (
            <label key={k} className="pill" style={{ cursor: "pointer" }}><input type="checkbox" checked={f[k]} onChange={(e) => set(k, e.target.checked)} /> {label}</label>
          ))}
        </div>
      </div>
      <div className="panel panel-pad stack">
        <h2>Rubric weights</h2>
        <div className="grid-2">{DIMS.map((k) => (
          <label key={k} className="field"><span>{DIM_NAMES[k]}</span><input className="input" type="number" min={0} max={100} value={weights[k]} onChange={(e) => setWeights({ ...weights, [k]: +e.target.value })} /></label>
        ))}</div>
        <p className={total === 100 ? "muted" : "error-text"}>Total {total} / 100</p>
      </div>
      <div className="panel panel-pad stack">
        <h2>Timing</h2>
        <label className="pill" style={{ cursor: "pointer", justifySelf: "start" }}><input type="checkbox" checked={f.custom} onChange={(e) => set("custom", e.target.checked)} /> Customise the phase plan</label>
        {!f.custom && <p className="muted">Default: warm-up 0:00–1:00, fundamentals –5:00, depth –10:00, applied problem –13:00, wrap-up –14:30, hard stop at 15:00.</p>}
        {f.custom && (
          <>
            <table className="tbl"><thead><tr><th>Phase</th><th>Starts (s)</th><th>Ends (s)</th></tr></thead>
              <tbody>{phases.map((p, i) => (
                <tr key={p.id}><td>{p.id}</td>
                  <td><input className="input" type="number" value={p.start} onChange={(e) => setPhases(phases.map((x, j) => j === i ? { ...x, start: +e.target.value } : x))} /></td>
                  <td><input className="input" type="number" value={p.end} onChange={(e) => setPhases(phases.map((x, j) => j === i ? { ...x, end: +e.target.value } : x))} /></td></tr>
              ))}</tbody></table>
            <label className="field"><span>Why this change? (required, shown with every result)</span><textarea className="input" value={f.justification} onChange={(e) => set("justification", e.target.value)} required /></label>
          </>
        )}
      </div>
      <div className="panel panel-pad stack">
        <h2>Who can take it</h2>
        <label className="field"><span>Roster (optional): one “roll number, name” per line</span>
          <textarea className="input" value={f.roster} onChange={(e) => set("roster", e.target.value)} placeholder={"22CS1047, Asha Verma\n22CS1048, Rahul Nair"} />
          <small>Leave empty to let anyone with the code join.</small>
        </label>
      </div>
      {err && <p className="error-text" role="alert">{err}</p>}
      <div className="row"><button className="btn" disabled={busy || total !== 100 || !f.bank_id}>{busy ? "Creating…" : "Create viva"}</button>
        <button type="button" className="btn quiet" onClick={() => navigate("/faculty")}>Cancel</button></div>
    </form>
  );
}
