import { useState } from "react";
import { api, del, get, patch, post } from "../api";
import { Empty, navigate, Spinner, toast, useAsync } from "../ui";

const PHASES = ["warmup", "fundamentals", "depth", "applied"];
const PHASE_LABEL: Record<string, string> = { warmup: "Warm-up (project opener)", fundamentals: "Fundamentals", depth: "Depth", applied: "Applied problems" };

export function Banks() {
  const { data, error, loading, reload } = useAsync(() => get("/api/banks"), []);
  const [form, setForm] = useState({ name: "", subject: "" });
  const [open, setOpen] = useState(false);
  async function create(e: React.FormEvent) {
    e.preventDefault();
    try { const r = await post("/api/banks", form); navigate(`/faculty/banks/${r.bank.id}`); } catch (ex: any) { toast(ex.message); }
  }
  return (
    <>
      <div className="page-head">
        <div><h1>Question banks</h1><p className="sub">The examiner only asks approved questions, or logged variants of them.</p></div>
        <button className="btn" onClick={() => setOpen(!open)}>New bank</button>
      </div>
      {open && (
        <form className="panel panel-pad row" onSubmit={create} style={{ marginBottom: 16, alignItems: "flex-end" }}>
          <label className="field" style={{ flex: 1 }}><span>Name</span><input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required /></label>
          <label className="field" style={{ flex: 1 }}><span>Subject</span><input className="input" value={form.subject} onChange={(e) => setForm({ ...form, subject: e.target.value })} required /></label>
          <button className="btn">Create</button>
        </form>
      )}
      {loading && <Spinner />}
      {error && <p className="error-text">{error}</p>}
      {data && !data.banks.length && <div className="panel"><Empty>No banks yet.</Empty></div>}
      <div className="vivas">
        {data?.banks.map((b: any) => (
          <div key={b.id} className="panel viva-row" style={{ gridTemplateColumns: "minmax(0,2fr) repeat(4, minmax(70px, auto)) auto" }} role="link" tabIndex={0}
            onClick={() => navigate(`/faculty/banks/${b.id}`)} onKeyDown={(e) => e.key === "Enter" && navigate(`/faculty/banks/${b.id}`)}>
            <div><div className="t">{b.name}</div><div className="s">{b.subject}, version {b.version}</div></div>
            <div className="stat"><b>{b.by_phase?.fundamentals ?? 0}</b><span>fundamentals</span></div>
            <div className="stat"><b>{b.by_phase?.depth ?? 0}</b><span>depth</span></div>
            <div className="stat"><b>{b.by_phase?.applied ?? 0}</b><span>applied</span></div>
            <div className="stat"><b className={b.drafts ? "" : "muted"}>{b.drafts}</b><span>drafts</span></div>
            {b.ready ? <span className="pill ok">Ready</span> : <span className="pill warn">Not ready</span>}
          </div>
        ))}
      </div>
    </>
  );
}

export function BankEditor({ id }: { id: number }) {
  const { data, error, loading, reload } = useAsync(() => get(`/api/banks/${id}`), [id]);
  const [status, setStatus] = useState("all");
  const [edit, setEdit] = useState<any>(null);
  const [gen, setGen] = useState(false);
  const [selected, setSelected] = useState<number[]>([]);
  if (loading && !data) return <Spinner />;
  if (error) return <p className="error-text">{error}</p>;
  const b = data.bank;
  const qs = data.questions.filter((q: any) => status === "all" || q.status === status);

  async function upload(file: File) {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("approve", "true");
    try {
      const r = await api(`/api/banks/${id}/import`, { form: fd });
      toast(`Imported: ${r.added} added, ${r.replaced} updated${r.errors.length ? `, ${r.errors.length} rows skipped` : ""}.`);
      if (r.errors.length) alert("Rows skipped:\n" + r.errors.join("\n"));
      reload();
    } catch (e: any) { toast(e.message); }
  }
  async function setQs(ids: number[], st: string) {
    await post(`/api/banks/${id}/status`, { ids, status: st });
    setSelected([]);
    reload();
  }

  return (
    <>
      <div className="page-head">
        <div><h1>{b.name}</h1><p className="sub">{b.subject}. Version {b.version}: changes to approved questions create a new version; past vivas keep the exact text they used.</p></div>
        <div className="row">
          <label className="btn quiet" style={{ cursor: "pointer" }}>Upload CSV or JSON<input type="file" accept=".csv,.json" hidden onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} /></label>
          <button className="btn quiet" onClick={() => setGen(true)}>Draft from syllabus</button>
          <a className="btn quiet" href={`/api/banks/${id}/export?format=csv`}>Export CSV</a>
          <button className="btn" onClick={() => setEdit({ topic: "", phase: "fundamentals", type: "concept", difficulty: 2, question: "", rephrase: "", key_points: [{ point: "", terms: [] }], follow_ups: [], hints: [""], misconceptions: [], code_snippet: "" })}>Add question</button>
        </div>
      </div>
      <div className={b.ready ? "panel panel-pad stack" : "review-reasons"} style={{ marginBottom: 16 }}>
        {b.ready ? <p><span className="pill ok">Ready for vivas</span> <span className="muted">{b.approved} approved questions across {Object.keys(b.topics || {}).length} topics.</span></p>
          : <><b>Not ready yet</b>{b.errors.map((e: string) => <span key={e}>{e}</span>)}</>}
        {b.warnings.length > 0 && (
          <details>
            <summary className="muted" style={{ cursor: "pointer" }}>{b.warnings.length} suggestion{b.warnings.length > 1 ? "s" : ""} to improve the bank</summary>
            <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>{b.warnings.map((w: string) => <li key={w}>{w}</li>)}</ul>
          </details>
        )}
      </div>
      <div className="row" style={{ marginBottom: 12 }}>
        {[["all", "All"], ["approved", "Approved"], ["draft", `Drafts (${b.drafts})`], ["rejected", "Rejected"]].map(([k, l]) => (
          <button key={k} className={`btn small ${status === k ? "" : "quiet"}`} onClick={() => setStatus(k)}>{l}</button>
        ))}
        <span style={{ flex: 1 }} />
        {selected.length > 0 && <>
          <span className="muted">{selected.length} selected</span>
          <button className="btn small" onClick={() => setQs(selected, "approved")}>Approve</button>
          <button className="btn small quiet" onClick={() => setQs(selected, "rejected")}>Reject</button>
        </>}
      </div>
      {PHASES.map((ph) => {
        const items = qs.filter((q: any) => q.phase === ph);
        if (!items.length) return null;
        return (
          <section key={ph} className="panel" style={{ marginBottom: 16 }}>
            <div className="panel-pad" style={{ paddingBottom: 8 }}><h2>{PHASE_LABEL[ph]} <span className="muted" style={{ fontWeight: 500 }}>({items.length})</span></h2></div>
            {items.map((q: any) => (
              <div key={q.id} className="bank-q">
                <label className="row" style={{ alignItems: "flex-start" }}>
                  <input type="checkbox" checked={selected.includes(q.id)} onChange={(e) => setSelected(e.target.checked ? [...selected, q.id] : selected.filter((x) => x !== q.id))} aria-label={`Select ${q.code}`} />
                  <span className="code">{q.code}</span>
                </label>
                <div>
                  <div className="q">{q.question}</div>
                  <div className="meta">{q.topic}, difficulty {q.difficulty}, {q.key_points.length} key points, {q.follow_ups.length} follow-ups{q.hints.length ? ", hint" : ", no hint"}{q.source === "ai" ? ", AI draft" : ""}</div>
                </div>
                <div className="row">
                  {q.status !== "approved" ? <span className="pill warn">{q.status}</span> : <span className="pill ok">approved</span>}
                  {q.status === "draft" && <button className="btn small" onClick={() => setQs([q.id], "approved")}>Approve</button>}
                  <button className="btn small quiet" onClick={() => setEdit(q)}>Edit</button>
                </div>
              </div>
            ))}
          </section>
        );
      })}
      {edit && <QuestionDrawer bankId={id} q={edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); reload(); }} />}
      {gen && <GenerateDrawer bankId={id} onClose={() => setGen(false)} onDone={() => { setGen(false); setStatus("draft"); reload(); }} />}
    </>
  );
}

function QuestionDrawer({ bankId, q, onClose, onSaved }: { bankId: number; q: any; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState<any>(JSON.parse(JSON.stringify(q)));
  const [err, setErr] = useState("");
  const set = (k: string, v: any) => setF((x: any) => ({ ...x, [k]: v }));
  async function save() {
    setErr("");
    const body = { ...f, hints: (f.hints || []).filter((h: string) => h.trim()),
      key_points: f.key_points.filter((k: any) => k.point.trim()).map((k: any) => ({ ...k, terms: typeof k.terms === "string" ? k.terms.split("/").map((t: string) => t.trim()).filter(Boolean) : k.terms })),
      follow_ups: (f.follow_ups || []).filter((x: any) => x.text?.trim()) };
    try {
      if (f.id) await patch(`/api/banks/questions/${f.id}`, body); else await post(`/api/banks/${bankId}/questions`, body);
      toast("Saved"); onSaved();
    } catch (e: any) { setErr(e.message); }
  }
  async function remove() {
    if (!window.confirm("Delete this question?")) return;
    await del(`/api/banks/questions/${f.id}`); onSaved();
  }
  return (
    <>
      <div className="drawer-bg" onClick={onClose} />
      <div className="drawer" role="dialog" aria-label="Edit question">
        <div className="row" style={{ justifyContent: "space-between" }}><h2 style={{ fontSize: 20 }}>{f.code || "New question"}</h2><button className="btn quiet small" onClick={onClose}>Close</button></div>
        <div className="grid-2">
          <label className="field"><span>Topic</span><input className="input" value={f.topic} onChange={(e) => set("topic", e.target.value)} /></label>
          <label className="field"><span>Phase</span><select className="input" value={f.phase} onChange={(e) => set("phase", e.target.value)}>{PHASES.map((p) => <option key={p}>{p}</option>)}</select></label>
          <label className="field"><span>Type</span><select className="input" value={f.type} onChange={(e) => set("type", e.target.value)}>{["concept", "scenario", "debug", "project"].map((p) => <option key={p}>{p}</option>)}</select></label>
          <label className="field"><span>Difficulty</span><select className="input" value={f.difficulty} onChange={(e) => set("difficulty", +e.target.value)}>{[1, 2, 3].map((p) => <option key={p} value={p}>{["", "Easy", "Medium", "Hard"][p]}</option>)}</select></label>
        </div>
        <label className="field"><span>Question (spoken aloud)</span><textarea className="input" value={f.question} onChange={(e) => set("question", e.target.value)} /></label>
        <label className="field"><span>Rephrased version (used when a student asks what the question means)</span><input className="input" value={f.rephrase} onChange={(e) => set("rephrase", e.target.value)} /></label>
        <div className="field"><span>Key points: what a complete answer covers, and words students use for each (separate with /)</span>
          {f.key_points.map((k: any, i: number) => (
            <div key={i} className="kp-row">
              <input className="input" value={k.point} placeholder="Atomicity: all or nothing" onChange={(e) => set("key_points", f.key_points.map((x: any, j: number) => j === i ? { ...x, point: e.target.value } : x))} />
              <input className="input" value={Array.isArray(k.terms) ? k.terms.join(" / ") : k.terms} placeholder="atomic / all or nothing / rollback" onChange={(e) => set("key_points", f.key_points.map((x: any, j: number) => j === i ? { ...x, terms: e.target.value } : x))} />
              <button className="btn quiet small" onClick={() => set("key_points", f.key_points.filter((_: any, j: number) => j !== i))} aria-label="Remove key point">Remove</button>
            </div>
          ))}
          <button className="btn link" style={{ justifySelf: "start" }} onClick={() => set("key_points", [...f.key_points, { point: "", terms: [] }])}>Add key point</button>
        </div>
        <div className="field"><span>Follow-ups (asked after a strong answer)</span>
          {(f.follow_ups || []).map((fu: any, i: number) => (
            <div key={i} className="kp-row" style={{ gridTemplateColumns: "120px 1fr auto" }}>
              <select className="input" value={fu.kind} onChange={(e) => set("follow_ups", f.follow_ups.map((x: any, j: number) => j === i ? { ...x, kind: e.target.value } : x))}>{["why", "what_if", "deeper", "example", "probe"].map((k) => <option key={k}>{k}</option>)}</select>
              <input className="input" value={fu.text} onChange={(e) => set("follow_ups", f.follow_ups.map((x: any, j: number) => j === i ? { ...x, text: e.target.value } : x))} />
              <button className="btn quiet small" onClick={() => set("follow_ups", f.follow_ups.filter((_: any, j: number) => j !== i))}>Remove</button>
            </div>
          ))}
          <button className="btn link" style={{ justifySelf: "start" }} onClick={() => set("follow_ups", [...(f.follow_ups || []), { kind: "why", text: "", key_points: [] }])}>Add follow-up</button>
        </div>
        <label className="field"><span>Hint (given once after a partial answer; must not name the answer)</span><input className="input" value={(f.hints || [""])[0] || ""} onChange={(e) => set("hints", [e.target.value])} /></label>
        <label className="field"><span>Code shown on screen (applied problems, optional)</span><textarea className="input" style={{ fontFamily: "var(--mono)" }} value={f.code_snippet} onChange={(e) => set("code_snippet", e.target.value)} /></label>
        {err && <p className="error-text">{err}</p>}
        <div className="row"><button className="btn" onClick={save}>Save</button>{f.id && <button className="btn link" onClick={remove}>Delete question</button>}</div>
      </div>
    </>
  );
}

function GenerateDrawer({ bankId, onClose, onDone }: { bankId: number; onClose: () => void; onDone: () => void }) {
  const [syllabus, setSyllabus] = useState("");
  const [counts, setCounts] = useState({ fundamentals: 6, depth: 3, applied: 2 });
  const [state, setState] = useState<"idle" | "running" | "done">("idle");
  const [msg, setMsg] = useState("");
  async function run() {
    setState("running");
    try {
      const r = await post(`/api/banks/${bankId}/generate`, { syllabus, ...counts });
      for (let i = 0; i < 120; i++) {
        await new Promise((res) => setTimeout(res, 1500));
        const t = (await get(`/api/banks/tasks/${r.task_id}`)).task;
        if (t.status === "done") { setMsg(`${t.result.drafts} draft questions added${t.result.offline ? " (offline template generator: please edit them)" : ""}. Review and approve them before use.`); setState("done"); return; }
        if (t.status === "failed") { setMsg(t.error); setState("idle"); return; }
      }
    } catch (e: any) { setMsg(e.message); setState("idle"); }
  }
  return (
    <>
      <div className="drawer-bg" onClick={onClose} />
      <div className="drawer" role="dialog" aria-label="Draft questions from a syllabus">
        <div className="row" style={{ justifyContent: "space-between" }}><h2 style={{ fontSize: 20 }}>Draft questions from a syllabus</h2><button className="btn quiet small" onClick={onClose}>Close</button></div>
        <p className="muted">Paste the syllabus or unit outline. The AI writes draft questions with key points, follow-ups and a hint; hints that would give away the answer are removed automatically. Drafts are never asked until you approve them.</p>
        <label className="field"><span>Syllabus</span><textarea className="input" style={{ minHeight: 220 }} value={syllabus} onChange={(e) => setSyllabus(e.target.value)} placeholder={"Unit 3: Transactions: ACID properties, schedules, serializability\nUnit 4: Concurrency control: locking, two-phase locking, deadlocks"} /></label>
        <div className="grid-2">{Object.entries(counts).map(([k, v]) => (
          <label key={k} className="field"><span>{k}</span><input className="input" type="number" min={0} max={20} value={v} onChange={(e) => setCounts({ ...counts, [k]: +e.target.value })} /></label>
        ))}</div>
        {msg && <p className={state === "done" ? "" : "error-text"}>{msg}</p>}
        <div className="row">
          {state !== "done" ? <button className="btn" disabled={syllabus.length < 20 || state === "running"} onClick={run}>{state === "running" ? "Drafting…" : "Draft questions"}</button>
            : <button className="btn" onClick={onDone}>Review drafts</button>}
        </div>
      </div>
    </>
  );
}
