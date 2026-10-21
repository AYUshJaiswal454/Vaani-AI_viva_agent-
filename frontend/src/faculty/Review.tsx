import { useEffect, useMemo, useRef, useState } from "react";
import { get, mmss, post, when } from "../api";
import { DIM_NAMES, DIMS, Dots, navigate, Spinner, statusPill, toast, useAsync } from "../ui";

const LABEL_TEXT: Record<string, string> = { strong: "strong", partial: "partial", weak: "vague", idk: "don't know", offtopic: "off-topic", empty: "inaudible", repeat: "asked to repeat", clarify: "asked to clarify", think: "asked for time", identity: "identity", wrapup: "wrap-up" };
const KIND_TEXT: Record<string, string> = { question: "Question", followup: "Follow-up", hint: "Hint", rephrase: "Rephrased", repeat: "Repeated", redirect: "Redirect", prompt: "Prompt", wrapup: "Wrap-up", close: "Close", ack: "Acknowledgement" };

export function Review({ id }: { id: number }) {
  const blindParam = new URLSearchParams(location.search).get("blind") === "1";
  const { data, error, loading, reload } = useAsync(() => get(`/api/sessions/${id}${blindParam ? "?blind=1" : ""}`), [id, blindParam]);
  const audio = useRef<HTMLAudioElement>(null);
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [evidence, setEvidence] = useState<number[]>([]);
  const [follow, setFollow] = useState(true);
  const [rate, setRate] = useState(1);
  const lineRefs = useRef<Record<number, HTMLDivElement | null>>({});

  const current = useMemo(() => {
    if (!data) return -1;
    let cur = -1;
    for (const l of data.turns) { if (l.t <= t + 0.05) cur = l.seq; else break; }
    return cur;
  }, [t, data]);

  useEffect(() => {
    if (follow && playing && current > 0) lineRefs.current[current]?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [current, follow, playing]);

  if (loading && !data) return <Spinner label="Loading the viva" />;
  if (error) return <p className="error-text">{error}</p>;
  const s = data.session;
  const sc = data.scores;
  const final = sc?.final;
  const ai = sc?.ai;
  const rule = sc?.rule;
  const override = final?.kind === "override" ? final : null;

  function seek(sec: number, play = true) {
    const a = audio.current;
    if (!a) return;
    a.currentTime = Math.max(0, sec - 0.2);
    if (play) a.play().catch(() => undefined);
  }
  function showEvidence(lines: number[]) {
    setEvidence(lines);
    const first = data.turns.find((l: any) => l.seq === lines[0]);
    if (first) { seek(first.t); lineRefs.current[first.seq]?.scrollIntoView({ block: "center", behavior: "smooth" }); }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <p className="muted" style={{ marginBottom: 6 }}><a href={`/faculty/vivas/${data.viva.id}`} onClick={(e) => { e.preventDefault(); navigate(`/faculty/vivas/${data.viva.id}`); }}>{data.viva.title}</a></p>
          <h1>{s.student} <span className="muted" style={{ fontWeight: 500, fontSize: 20 }}>{s.roll_no}</span> <span style={{ verticalAlign: "middle" }}>{statusPill(s.status, s.needs_review)}</span></h1>
          <p className="sub">{when(s.started_at)}{s.end_reason ? `, ended: ${s.end_reason.replace(/_/g, " ")}` : ""}</p>
        </div>
        <div>
          {!data.blind && final && (
            <div style={{ textAlign: "right" }}>
              <div className={`score-big ${override ? "redpen" : ""}`}>{final.total}<small>/100</small></div>
              <div className="muted" style={{ fontSize: 13 }}>{override ? `Changed by ${override.reviewer_name}; AI suggested ${ai?.total}` : `AI score${final.confidence ? `, ${final.confidence} confidence` : ""}`}</div>
            </div>
          )}
        </div>
      </div>

      {!data.blind && ai?.needs_review && s.needs_review && (
        <div className="review-reasons" style={{ marginBottom: 18 }}>
          <b>Faculty review is required before this mark is released</b>
          {ai.review_reasons.map((r: any) => <span key={r.text}>{r.mandatory ? "" : "(note) "}{r.text}</span>)}
          <div className="row" style={{ marginTop: 6 }}>
            <button className="btn small" onClick={async () => { await post(`/api/sessions/${id}/accept`, {}); toast("Marked as reviewed with the AI score."); reload(); }}>Accept the AI score</button>
            <span className="muted">or change marks in the red-pen panel</span>
          </div>
        </div>
      )}

      <div className="review">
        <section className="panel" aria-label="Recording and transcript">
          <div className="player">
            {s.audio_url ? (
              <>
                <audio ref={audio} src={s.audio_url} preload="metadata" onTimeUpdate={(e) => setT((e.target as HTMLAudioElement).currentTime)}
                  onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} />
                <Waveform url={s.waveform_url} t={t} onSeek={(sec) => seek(sec)} turns={data.turns} />
                <div className="row">
                  <button className="btn small" onClick={() => (playing ? audio.current?.pause() : audio.current?.play())}>{playing ? "Pause" : "Play"}</button>
                  <span className="time">{mmss(t)} / {mmss(s.recording?.duration_s)}</span>
                  <select className="input" style={{ width: 84 }} value={rate} onChange={(e) => { setRate(+e.target.value); if (audio.current) audio.current.playbackRate = +e.target.value; }} aria-label="Playback speed">
                    {[0.75, 1, 1.25, 1.5, 2].map((r) => <option key={r} value={r}>{r}×</option>)}
                  </select>
                  <label className="row muted" style={{ fontSize: 14 }}><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> Follow along</label>
                  {s.recording?.examiner_voice_rebuilt && <span className="muted" style={{ fontSize: 13 }}>Examiner voice rebuilt for replay (the student heard their browser's voice)</span>}
                </div>
              </>
            ) : <p className="muted">{s.recording_erased_at ? `The recording was erased on ${when(s.recording_erased_at)}.` : s.status === "live" ? "Viva in progress. The recording appears when it ends." : "The recording is being prepared."}</p>}
          </div>
          <div className="transcript">
            {data.turns.map((l: any) => {
              const a = l.meta?.answer;
              const isEv = evidence.includes(l.seq);
              return (
                <div key={l.seq} ref={(el) => { lineRefs.current[l.seq] = el; }} className={`line ${l.speaker} ${current === l.seq ? "playing" : ""} ${isEv ? "evidence" : ""}`}
                  onClick={() => seek(l.t)} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && seek(l.t)}>
                  <span className="lid">{l.id}</span>
                  <span className="ts">{l.mmss}</span>
                  <div>
                    <span className="who">{l.speaker === "examiner" ? `Examiner · ${KIND_TEXT[l.kind] || l.kind}` : "Student"}</span>
                    <div className="txt">{l.text}</div>
                    <div className="tags">
                      {l.speaker === "examiner" && (l.source || "").split(",").filter((x: string) => x.startsWith("bank:") || x.startsWith("variant:")).map((x: string) => (
                        <span key={x} className={`tag ${x.startsWith("variant:") ? "var" : "src"}`} title={x.startsWith("variant:") ? "AI-phrased variant of this bank item" : "From the approved question bank"}>{x.replace("bank:", "").replace("variant:", "variant of ")}</span>
                      ))}
                      {a?.label && <span className={`tag lab-${a.label}`}>{LABEL_TEXT[a.label] || a.label}{typeof a.coverage === "number" && a.label !== "idk" ? `, ${Math.round(a.coverage * 100)}% of key points` : ""}</span>}
                      {a?.after_hint && <span className="tag">after hint</span>}
                      {a?.cut && <span className="tag">cut at 90 s</span>}
                      {l.meta?.stt?.confidence != null && l.meta.stt.confidence < 0.5 && <span className="tag lab-weak">unclear audio ({Math.round(l.meta.stt.confidence * 100)}%)</span>}
                    </div>
                    {l.speaker === "examiner" && l.meta?.reason && <div className="why">Why: {l.meta.reason}</div>}
                    {l.meta?.variant?.used && <div className="why">Bank wording: “{l.meta.segments?.find((x: any) => x.base)?.base}”</div>}
                    {l.meta?.scratchpad && <pre className="why" style={{ whiteSpace: "pre-wrap", fontFamily: "var(--mono)" }}>{l.meta.scratchpad}</pre>}
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        <aside className="stack">
          {data.blind ? <BlindMarks id={id} rubric={data.rubric} onDone={() => navigate(`/faculty/sessions/${id}`)} /> : (
            <>
              <section className="panel" aria-label="Scores">
                <div className="panel-pad" style={{ paddingBottom: 4 }}>
                  <h2>Rubric scores</h2>
                  {!final && <p className="muted" style={{ marginTop: 8 }}>{s.status === "live" ? "Scores appear after the viva ends." : "Scoring…"}</p>}
                </div>
                {final && DIMS.map((k) => {
                  const d = final.dims[k];
                  const w = data.rubric.dimensions.find((x: any) => x.id === k)?.weight;
                  const aiD = ai?.dims?.[k];
                  const ruleD = rule?.dims?.[k];
                  return (
                    <div key={k} className="dim">
                      <div className="dim-head"><b>{DIM_NAMES[k]}<span className="w">{w}%</span></b><Dots value={d.score} pen={!!override && d.score !== d.ai_score} /></div>
                      <p className="reason">{d.reason}</p>
                      {d.evidence?.length > 0 && <div className="chips">{d.evidence.map((e: any) => (
                        <button key={e.line} className="chip" onClick={() => showEvidence(d.evidence.map((x: any) => x.line))}>L{String(e.line).padStart(2, "0")} · {mmss(e.t)}</button>
                      ))}</div>}
                      <p className="compare">
                        {override && d.score !== d.ai_score ? `AI gave ${d.ai_score}. ` : ""}
                        {ruleD && aiD && ai?.model !== "rule-scorer" ? `Rule-based cross-check: ${ruleD.score}. ` : ""}
                        {aiD?.spread ? `AI runs varied by ${aiD.spread} (${(aiD.runs || []).join(", ")}).` : ""}
                      </p>
                    </div>
                  );
                })}
              </section>
              {final?.feedback?.length > 0 && (
                <section className="panel panel-pad stack" aria-label="Feedback">
                  <h2>Feedback for the student</h2>
                  <ul className="feedback-list">{final.feedback.map((f: any, i: number) => (
                    <li key={i}><span>{f.text}</span>{f.evidence?.length > 0 && <div className="chips">{f.evidence.map((e: string) => (
                      <button key={e} className="chip" onClick={() => showEvidence([parseInt(e.slice(1), 10)])}>{e}</button>))}</div>}</li>
                  ))}</ul>
                </section>
              )}
              {final && <Override id={id} final={final} rubric={data.rubric} onSaved={reload} />}
              <MyMarks id={id} marks={data.my_marks} />
            </>
          )}
          <section className="panel panel-pad stack" aria-label="Integrity">
            <h2>Integrity signals</h2>
            {!data.integrity.length ? <p className="muted">None recorded.</p> : (
              <div className="timeline">{data.integrity.map((e: any) => (
                <div key={e.id} className="ev"><button className="btn link" onClick={() => seek(e.t)}>{mmss(e.t)}</button>
                  <span>{e.severity === "warn" ? <span className="pill warn">warning</span> : null} {e.kind.replace(/_/g, " ")}{e.detail?.gap_s ? ` (${e.detail.gap_s} s)` : ""}{e.detail?.heard ? `: heard “${e.detail.heard}”` : ""}</span></div>
              ))}</div>
            )}
            <p className="muted" style={{ fontSize: 13 }}>Signals never change marks automatically.</p>
          </section>
          <section className="panel panel-pad stack" aria-label="Audit details">
            <h2>Audit details</h2>
            <dl className="kv">
              <dt>Scored by</dt><dd>{final?.model || "–"}</dd>
              <dt>Prompt version</dt><dd>{final?.prompt_version || "–"}</dd>
              <dt>Question bank</dt><dd>version {s.bank_version}</dd>
              <dt>Examiner engine</dt><dd>{s.engine_version}</dd>
              <dt>Speech</dt><dd>{s.settings.stt_engine}{s.settings.whisper_model && s.settings.stt_engine === "whisper-onnx" ? ` (${s.settings.whisper_model})` : ""}, voice {s.settings.tts_engine}</dd>
              <dt>Reply latency</dt><dd>{s.latency.replies ? `p50 ${s.latency.p50_ms} ms, p95 ${s.latency.p95_ms} ms over ${s.latency.replies} replies` : "–"}</dd>
              <dt>Clock paused</dt><dd>{s.paused_s ? `${Math.round(s.paused_s)} s (network)` : "no"}</dd>
              <dt>Depth topic</dt><dd>{s.depth_topic || "–"}</dd>
              <dt>AI cost</dt><dd>${s.cost_usd}</dd>
              <dt>Consent</dt><dd>{s.consent?.accepted_at ? `${when(s.consent.accepted_at)} (${s.consent.version})` : "–"}</dd>
            </dl>
            {sc?.history?.length > 1 && (
              <>
                <h2 style={{ marginTop: 6 }}>Score history</h2>
                <table className="tbl"><tbody>{sc.history.map((h: any) => (
                  <tr key={h.id}><td>{h.kind === "override" ? <span className="redpen">Override by {h.reviewer_name}</span> : h.kind === "ai" ? "AI score" : "Rule cross-check"}</td>
                    <td className="r num">{h.total}</td><td className="muted">{when(h.created_at)}</td></tr>
                ))}</tbody></table>
                {override && <p><b className="redpen">Reason:</b> {override.reason}</p>}
              </>
            )}
            <div className="row">
              <a className="btn quiet small" href={`/api/sessions/${id}/report.json`}>Download full record</a>
              <button className="btn quiet small" onClick={async () => { await post(`/api/sessions/${id}/rescore`); toast("Rescoring queued"); }}>Rescore</button>
            </div>
            {data.can_erase && <EraseBox id={id} hasRecording={!!s.audio_url} />}
            <AuditTrail id={id} />
          </section>
        </aside>
      </div>
    </>
  );
}

function AuditTrail({ id }: { id: number }) {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<any[] | null>(null);
  useEffect(() => { if (open) get(`/api/sessions/${id}/audit`).then((d) => setRows(d.entries)).catch(() => setRows([])); }, [open, id]);
  return (
    <details className="audit" onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary>Audit trail</summary>
      {!rows ? <p className="muted">Loading…</p> : rows.length === 0 ? <p className="muted">Nothing recorded yet.</p> : (
        <ol className="audit-list">{rows.map((r) => (
          <li key={r.id}>
            <span className="muted num">{when(r.at)}</span> <b>{r.who}</b>: {r.what}
            {r.action === "score_override" && r.after && <span className="redpen"> (total {r.before?.total ?? "–"} → {r.after.total}{r.data?.reason ? `, “${r.data.reason}”` : ""})</span>}
            {r.data?.reason && r.action !== "score_override" && <span className="muted">: {r.data.reason}</span>}
            {r.data?.track && <span className="muted"> ({r.data.track} track)</span>}
          </li>
        ))}</ol>
      )}
    </details>
  );
}

function EraseBox({ id, hasRecording }: { id: number; hasRecording: boolean }) {
  const [open, setOpen] = useState(false);
  const [scope, setScope] = useState<"recording" | "everything">(hasRecording ? "recording" : "everything");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  if (!open) return <button className="btn quiet small danger-text" onClick={() => setOpen(true)}>Erase data…</button>;
  async function erase() {
    setBusy(true);
    try {
      await post(`/api/sessions/${id}/erase`, { scope, reason });
      toast(scope === "everything" ? "Attempt erased" : "Recording erased");
      if (scope === "everything") history.back(); else location.reload();
    } catch (e: any) { toast(e.message || "Could not erase"); setBusy(false); }
  }
  return (
    <div className="erase-box">
      <p><b>Erase student data</b> (administrators only). This cannot be undone; the audit log records who erased it and why.</p>
      <label><input type="radio" checked={scope === "recording"} disabled={!hasRecording} onChange={() => setScope("recording")} /> The recording only (transcript and marks stay)</label>
      <label><input type="radio" checked={scope === "everything"} onChange={() => setScope("everything")} /> The whole attempt: recording, transcript, marks and signals</label>
      <label className="field"><span>Reason</span><textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="For example: erasure request from the student, ticket #123" /></label>
      <div className="row">
        <button className="btn small danger" disabled={busy || reason.trim().length < 5} onClick={erase}>{scope === "everything" ? "Erase the attempt" : "Erase the recording"}</button>
        <button className="btn quiet small" onClick={() => setOpen(false)}>Cancel</button>
      </div>
    </div>
  );
}

function Waveform({ url, t, onSeek, turns }: { url: string; t: number; onSeek: (s: number) => void; turns: any[] }) {
  const [wave, setWave] = useState<{ duration: number; peaks: number[] } | null>(null);
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => { if (url) get(url).then(setWave).catch(() => undefined); }, [url]);
  useEffect(() => {
    const c = ref.current;
    if (!c || !wave) return;
    const dpr = window.devicePixelRatio || 1;
    const w = c.clientWidth, h = c.clientHeight;
    c.width = w * dpr; c.height = h * dpr;
    const g = c.getContext("2d")!;
    g.scale(dpr, dpr);
    g.clearRect(0, 0, w, h);
    // speaker bands: examiner turns shaded
    for (let i = 0; i < turns.length; i++) {
      const l = turns[i];
      if (l.speaker !== "examiner") continue;
      const end = turns[i + 1]?.t ?? l.t + 4;
      g.fillStyle = "#f2c94c26";
      g.fillRect((l.t / wave.duration) * w, 0, ((end - l.t) / wave.duration) * w, h);
    }
    const n = wave.peaks.length;
    const pos = (t / wave.duration) * w;
    for (let i = 0; i < n; i++) {
      const x = (i / n) * w;
      const v = wave.peaks[i] * (h / 2 - 2);
      g.fillStyle = x < pos ? "#1e2a25" : "#a9b3ac";
      g.fillRect(x, h / 2 - v, Math.max(1, w / n - 0.5), v * 2 || 1);
    }
    g.fillStyle = "#2d6fb5";
    g.fillRect(pos, 0, 2, h);
  }, [wave, t, turns]);
  return <canvas ref={ref} className="wave" onClick={(e) => {
    if (!wave) return;
    const r = (e.target as HTMLCanvasElement).getBoundingClientRect();
    onSeek(((e.clientX - r.left) / r.width) * wave.duration);
  }} aria-label="Waveform: click to seek" />;
}

function Override({ id, final, rubric, onSaved }: { id: number; final: any; rubric: any; onSaved: () => void }) {
  const [dims, setDims] = useState<Record<string, number>>(Object.fromEntries(DIMS.map((k) => [k, final.dims[k].score])));
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const changed = DIMS.some((k) => dims[k] !== final.dims[k].score);
  const total = Math.round(DIMS.reduce((s, k) => s + (dims[k] / 5) * (rubric.dimensions.find((d: any) => d.id === k)?.weight || 0), 0) * 10) / 10;
  async function save() {
    setBusy(true); setErr("");
    try { await post(`/api/sessions/${id}/override`, { dims, reason }); toast("Marks updated. The change is logged with your name and reason."); setReason(""); onSaved(); }
    catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }
  return (
    <section className="panel panel-pad stack override" aria-label="Change marks">
      <h2>Change marks</h2>
      <div className="ovr-grid">
        {DIMS.map((k) => (
          <label key={k} style={{ display: "contents" }}>
            <span>{DIM_NAMES[k]}</span>
            <select className="input" value={dims[k]} onChange={(e) => setDims({ ...dims, [k]: +e.target.value })}>
              {[0, 1, 2, 3, 4, 5].map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          </label>
        ))}
      </div>
      <p>New total: <b className={changed ? "redpen" : ""}>{total}</b>/100</p>
      <label className="field"><span>Reason (required, saved with your name)</span>
        <textarea className="input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. The answer at 04:12 is correct; the recogniser misheard “B+ tree”." /></label>
      {err && <p className="error-text">{err}</p>}
      <button className="btn danger" disabled={!changed || reason.trim().length < 10 || busy} onClick={save}>Save changed marks</button>
    </section>
  );
}

function MyMarks({ id, marks }: { id: number; marks: any }) {
  if (marks) return (
    <section className="panel panel-pad stack" aria-label="Your calibration marks">
      <h2>Your calibration marks</h2>
      <p>{marks.total}/100{marks.blind ? ", entered before seeing the AI score" : ", entered after seeing the AI score"}.</p>
    </section>
  );
  return (
    <section className="panel panel-pad stack" aria-label="Calibration">
      <h2>Calibration</h2>
      <p className="muted">To measure how close the AI is to your judgement, mark this viva yourself without seeing the AI score.</p>
      <button className="btn quiet small" onClick={() => navigate(`/faculty/sessions/${id}?blind=1`)}>Mark blind</button>
    </section>
  );
}

function BlindMarks({ id, rubric, onDone }: { id: number; rubric: any; onDone: () => void }) {
  const [dims, setDims] = useState<Record<string, number>>(Object.fromEntries(DIMS.map((k) => [k, 3])));
  const [notes, setNotes] = useState("");
  const [err, setErr] = useState("");
  async function save() {
    try { await post(`/api/sessions/${id}/marks`, { dims, notes }); toast("Your marks are saved for calibration."); onDone(); }
    catch (e: any) { setErr(e.message); }
  }
  return (
    <section className="panel panel-pad stack" aria-label="Blind marking">
      <h2>Mark this viva blind</h2>
      <p className="muted">The AI score is hidden until you submit. Listen, read the transcript, then mark each dimension 0–5.</p>
      {rubric.dimensions.map((d: any) => (
        <label key={d.id} className="field"><span>{DIM_NAMES[d.id]} ({d.weight}%)</span>
          <select className="input" value={dims[d.id]} onChange={(e) => setDims({ ...dims, [d.id]: +e.target.value })}>
            {[0, 1, 2, 3, 4, 5].map((v) => <option key={v} value={v}>{v}: {d.anchors[String(v)]}</option>)}
          </select>
        </label>
      ))}
      <label className="field"><span>Notes (optional)</span><textarea className="input" value={notes} onChange={(e) => setNotes(e.target.value)} /></label>
      {err && <p className="error-text">{err}</p>}
      <button className="btn" onClick={save}>Submit my marks</button>
    </section>
  );
}
