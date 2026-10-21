import { useState } from "react";
import { get, post, when } from "../api";
import { DIM_NAMES, Empty, navigate, Spinner, statusPill, toast, useAsync } from "../ui";

export function VivaDetail({ id }: { id: number }) {
  const [filter, setFilter] = useState("");
  const [tab, setTab] = useState("attempts");
  const { data, error, loading, reload } = useAsync(() => get(`/api/vivas/${id}/sessions${filter ? `?filter=${filter}` : ""}`), [id, filter]);
  const [sheet, setSheet] = useState<any>(null);

  if (loading && !data) return <Spinner label="Loading" />;
  if (error) return <p className="error-text">{error}</p>;
  const v = data.viva;
  const link = `${location.origin}/?code=${v.access_code}`;

  async function setStatus(status: string) {
    try { await post(`/api/vivas/${id}/status`, { status }); toast(status === "open" ? "Viva opened: students can join now." : "Viva closed."); reload(); }
    catch (e: any) { toast(e.message); }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{v.title}</h1>
          <p className="sub">{v.subject}. Question bank “{v.bank.name}”, version {v.bank.version}.</p>
        </div>
        <div className="row">
          {statusPill(v.status)}
          {v.status === "open" ? <button className="btn quiet" onClick={() => setStatus("closed")}>Close viva</button>
            : <button className="btn" onClick={() => setStatus("open")}>Open viva</button>}
        </div>
      </div>
      <div className="panel panel-pad row" style={{ justifyContent: "space-between", marginBottom: 18 }}>
        <div className="row">
          <span className="muted">Students join at</span><b>{location.host}</b><span className="muted">with code</span><span className="code-chip">{v.access_code}</span>
        </div>
        <button className="btn quiet small" onClick={() => { navigator.clipboard?.writeText(link); toast("Join link copied"); }}>Copy join link</button>
      </div>
      <div className="tabs" role="tablist">
        {[["attempts", `Attempts (${v.attempts})`], ["setup", "Setup"], ["export", "Export"]].map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{label}</button>
        ))}
      </div>
      {tab === "attempts" && (
        <div className="panel">
          <div className="row panel-pad" style={{ paddingBottom: 6 }}>
            {[["", "All"], ["review", `Needs review (${v.needs_review})`], ["flagged", "Integrity warnings"]].map(([k, label]) => (
              <button key={k} className={`btn small ${filter === k ? "" : "quiet"}`} onClick={() => setFilter(k)}>{label}</button>
            ))}
            <span style={{ flex: 1 }} />
            <span className="muted">{v.live ? `${v.live} in progress now` : ""}</span>
            <button className="btn quiet small" onClick={reload}>Refresh</button>
          </div>
          {!data.sessions.length ? <Empty><p>No attempts yet. Open the viva and share the code.</p></Empty> : (
            <table className="tbl">
              <thead><tr><th>Student</th><th>Roll no.</th><th>Status</th><th className="r">Score</th><th>Integrity</th><th>Started</th></tr></thead>
              <tbody>{data.sessions.map((s: any) => (
                <tr key={s.id} className="click" onClick={() => navigate(`/faculty/sessions/${s.id}`)}>
                  <td><b>{s.student}</b></td><td>{s.roll_no}</td><td>{statusPill(s.status, s.needs_review)}</td>
                  <td className="r num"><b>{s.final_total ?? "–"}</b>{s.final_total != null && <span className="muted">/100</span>}</td>
                  <td>{s.integrity.warn ? <span className="pill warn">{s.integrity.warn} warning{s.integrity.warn > 1 ? "s" : ""}</span> : <span className="muted">none</span>}</td>
                  <td className="muted">{when(s.started_at)}</td>
                </tr>
              ))}</tbody>
            </table>
          )}
        </div>
      )}
      {tab === "setup" && (
        <div className="grid-2">
          <div className="panel panel-pad stack">
            <h2>Settings</h2>
            <dl className="kv">
              <dt>Difficulty</dt><dd>{v.difficulty === "mixed" ? "Adaptive" : v.difficulty}</dd>
              <dt>Topics</dt><dd>{v.topics.length ? v.topics.join(", ") : "Whole bank"}</dd>
              <dt>Language</dt><dd>{v.settings.language === "hinglish" ? "English or Hinglish" : "English"}</dd>
              <dt>Speech recognition</dt><dd>{v.settings.stt_engine}</dd>
              <dt>Examiner voice</dt><dd>{v.settings.tts_engine}</dd>
              <dt>AI follow-ups</dt><dd>{v.settings.live_llm ? "On" : "Off"}</dd>
              <dt>Integrity signals</dt><dd>{v.settings.integrity ? "On" : "Off"}</dd>
              <dt>Roster</dt><dd>{v.roster.length ? `${v.roster.length} students` : "Anyone with the code"}</dd>
            </dl>
          </div>
          <div className="panel panel-pad stack">
            <h2>Rubric</h2>
            <table className="tbl"><tbody>{v.rubric.dimensions.map((d: any) => <tr key={d.id}><td>{DIM_NAMES[d.id]}</td><td className="r num">{d.weight}%</td></tr>)}</tbody></table>
            <h2>Phase plan</h2>
            {v.phase_plan?.phases ? (
              <>
                <table className="tbl"><tbody>{v.phase_plan.phases.map((p: any) => <tr key={p.id}><td>{p.id}</td><td className="r num">{p.start}–{p.end} s</td></tr>)}</tbody></table>
                <p><b>Justification:</b> {v.phase_plan.justification}</p>
              </>
            ) : <p className="muted">Default 15-minute plan from the brief.</p>}
          </div>
        </div>
      )}
      {tab === "export" && (
        <div className="panel panel-pad stack" style={{ maxWidth: 760 }}>
          <h2>Results</h2>
          <p className="muted">One row per student: final and AI totals, each dimension, who overrode a mark and why, review flags and feedback.</p>
          <div className="row">
            <a className="btn" href={`/api/vivas/${id}/export.csv`}>Download CSV</a>
            <button className="btn quiet" onClick={async () => setSheet(await get(`/api/vivas/${id}/sheets-link`))}>Google Sheets link</button>
          </div>
          {sheet && (
            <div className="stack">
              <p>Paste this formula into cell A1 of a Google Sheet. It refreshes automatically for {sheet.expires_days} days. Anyone with the link can read the results, so share the sheet carefully.</p>
              <input className="input" readOnly value={sheet.formula} onFocus={(e) => e.target.select()} />
            </div>
          )}
        </div>
      )}
    </>
  );
}
