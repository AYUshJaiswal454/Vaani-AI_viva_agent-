import { useState } from "react";
import { get, post, when } from "../api";
import { Link, Spinner, useAsync } from "../ui";

export function System() {
  const { data, error, loading, reload } = useAsync(() => get("/api/system/status"), []);
  const cost = useAsync(() => get("/api/system/cost"), []);
  const [test, setTest] = useState<any>(null);
  if (loading) return <Spinner />;
  if (error) return <p className="error-text">{error}</p>;
  const lat = data.latency;
  return (
    <>
      <div className="page-head">
        <div><h1>System</h1><p className="sub">Which engines are running, how fast the examiner replies, and what it costs.</p></div>
        <button className="btn quiet" onClick={reload}>Refresh</button>
      </div>
      <div className="grid-2">
        <section className="panel panel-pad stack">
          <h2>Engines</h2>
          <dl className="kv">
            <dt>Speech recognition</dt><dd>{data.stt.default}{data.stt.default === "whisper-onnx" ? ` (Whisper ${data.stt["whisper-onnx"].model}, on this server)` : ""}</dd>
            <dt>Examiner voice</dt><dd>{data.tts.default} (replays use {data.tts.replay})</dd>
            <dt>Voice activity</dt><dd>{data.vad.silero ? "Silero VAD" : "energy detector (Silero model not found)"}</dd>
            <dt>Language model</dt><dd>{data.ai.provider === "mock" ? "none: deterministic examiner and rule-based scorer" : `${data.ai.provider}: ${data.ai.live_model} live, ${data.ai.scorer_model} for scoring`}</dd>
            <dt>Turn taking</dt><dd>reply after {data.turn_taking.endpoint_silence_ms} ms of silence ({data.turn_taking.endpoint_incomplete_ms} ms if the sentence sounds unfinished)</dd>
            <dt>Recordings</dt><dd>{data.privacy.encrypt_audio ? "encrypted at rest" : "not encrypted"}, kept {data.privacy.retention_days} days</dd>
          </dl>
          <div className="row"><button className="btn quiet small" onClick={async () => setTest(await post("/api/system/test-ai"))}>Test AI connection</button>{test && <span className={test.ok ? "" : "error-text"}>{test.provider}: {test.message}</span>}</div>
        </section>
        <section className="panel panel-pad stack">
          <h2>Right now</h2>
          <div className="row" style={{ gap: 32 }}>
            <div className="stat"><b style={{ fontSize: 26 }}>{data.live_sessions}</b><span>vivas in progress</span></div>
            <div className="stat"><b style={{ fontSize: 26 }}>{data.stt_queue}</b><span>recognition jobs waiting</span></div>
            <div className="stat"><b style={{ fontSize: 26 }}>{data.tasks.queued}</b><span>background tasks queued</span></div>
          </div>
          <h2>Examiner reply time</h2>
          <p className="muted">From the end of the student's speech to the examiner starting to speak, over the last {lat.replies} replies (includes the silence wait).</p>
          <div className="row" style={{ gap: 32 }}>
            <div className="stat"><b style={{ fontSize: 26 }}>{lat.client_p50_ms ?? lat.server_p50_ms ?? "–"} ms</b><span>median</span></div>
            <div className="stat"><b style={{ fontSize: 26 }}>{lat.client_p95_ms ?? lat.server_p95_ms ?? "–"} ms</b><span>95th percentile</span></div>
            <div className="stat"><b style={{ fontSize: 26 }}>{lat.stt_p50_ms ?? "–"} ms</b><span>recognition wait</span></div>
          </div>
        </section>
      </div>
      {cost.data && (
        <section className="panel panel-pad stack" style={{ marginTop: 18 }}>
          <h2>Cost so far</h2>
          <p>Average AI and speech cost per finished viva: <b>₹{cost.data.avg_inr_per_viva}</b> ({cost.data.finished_vivas} vivas). Projected for 350 students: <b>₹{cost.data.projection_350_inr}</b>.</p>
          <table className="tbl">
            <thead><tr><th>Feature</th><th>Provider</th><th>Model</th><th className="r">Calls</th><th className="r">Tokens in / out</th><th className="r">Audio</th><th className="r">Cost (USD)</th></tr></thead>
            <tbody>{cost.data.by_feature.map((r: any, i: number) => (
              <tr key={i}><td>{r.feature}</td><td>{r.provider}</td><td>{r.model}</td><td className="r num">{r.calls}</td><td className="r num">{r.tin || 0} / {r.tout || 0}</td>
                <td className="r num">{r.audio ? `${Math.round(r.audio / 60)} min` : "–"}</td><td className="r num">{(r.usd || 0).toFixed(4)}</td></tr>
            ))}</tbody>
          </table>
        </section>
      )}
      <AuditLog />
    </>
  );
}

function AuditLog() {
  const log = useAsync(() => get("/api/system/audit?limit=200"), []);
  if (!log.data) return null;                       // administrators only (faculty get 403 and see nothing)
  return (
    <section className="panel panel-pad stack" style={{ marginTop: 18 }}>
      <h2>Audit log</h2>
      <p className="muted">Append-only record of joins, playbacks, score views, overrides, exports and erasures (latest 200).</p>
      <table className="tbl">
        <thead><tr><th>When</th><th>Who</th><th>What</th><th>On</th><th>Details</th></tr></thead>
        <tbody>{log.data.entries.map((r: any) => (
          <tr key={r.id}><td className="num muted">{when(r.at)}</td><td>{r.who}</td><td>{r.what}</td>
            <td>{r.entity === "session" ? <Link to={`/faculty/sessions/${r.entity_id}`}>attempt {r.entity_id}</Link> : `${r.entity} ${r.entity_id}`}</td>
            <td className="muted">{r.data?.reason || r.data?.track || ""}</td></tr>
        ))}</tbody>
      </table>
    </section>
  );
}
