import { useRef, useState } from "react";
import { api, del, get, post } from "../api";
import { Spinner, toast, useAsync } from "../ui";

export function Lab() {
  const prompts = useAsync(() => get("/api/lab/prompts"), []);
  const clips = useAsync(() => get("/api/lab/clips"), []);
  const [lang, setLang] = useState<"en-IN" | "hi-en">("en-IN");
  const [i, setI] = useState(0);
  const [speaker, setSpeaker] = useState("");
  const [accent, setAccent] = useState("");
  const [consent, setConsent] = useState(false);
  const [rec, setRec] = useState<MediaRecorder | null>(null);
  const [engines, setEngines] = useState<string[]>([]);
  const [results, setResults] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const chunks = useRef<Blob[]>([]);
  const list: string[] = prompts.data?.[lang] || [];
  const sentence = list[i % Math.max(1, list.length)] || "";

  async function record() {
    if (rec) { rec.stop(); return; }
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    const r = new MediaRecorder(stream);
    chunks.current = [];
    r.ondataavailable = (e) => chunks.current.push(e.data);
    r.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      setRec(null);
      const fd = new FormData();
      fd.append("audio", new Blob(chunks.current, { type: r.mimeType }), "clip.webm");
      fd.append("reference", sentence);
      fd.append("speaker", speaker || "volunteer");
      fd.append("accent", accent);
      fd.append("language", lang);
      fd.append("consent", consent ? "true" : "false");
      try { await api("/api/lab/clips", { form: fd }); toast("Clip saved"); setI(i + 1); clips.reload(); } catch (e: any) { toast(e.message); }
    };
    r.start();
    setRec(r);
  }

  async function evaluate() {
    setBusy(true);
    try { setResults(await post("/api/lab/evaluate", { engines, with_prompt: true })); } catch (e: any) { toast(e.message); } finally { setBusy(false); }
  }

  const available = clips.data ? Object.entries(clips.data.engines).filter(([k, v]: any) => k !== "default" && k !== "browser" && v.available).map(([k]) => k) : [];

  return (
    <>
      <div className="page-head">
        <div><h1>Speech lab</h1><p className="sub">Measure how well speech recognition understands your students' accents. Volunteers read sentences aloud; the sentence is the reference text.</p></div>
      </div>
      <div className="grid-2">
        <section className="panel panel-pad stack">
          <h2>Record a clip</h2>
          <div className="grid-2">
            <label className="field"><span>Speaker label</span><input className="input" value={speaker} onChange={(e) => setSpeaker(e.target.value)} placeholder="Volunteer 1" /></label>
            <label className="field"><span>Accent / region (optional)</span><input className="input" value={accent} onChange={(e) => setAccent(e.target.value)} placeholder="Tamil Nadu" /></label>
          </div>
          <div className="row">
            <button className={`btn small ${lang === "en-IN" ? "" : "quiet"}`} onClick={() => { setLang("en-IN"); setI(0); }}>English</button>
            <button className={`btn small ${lang === "hi-en" ? "" : "quiet"}`} onClick={() => { setLang("hi-en"); setI(0); }}>Hinglish</button>
            <span className="muted">Sentence {(i % Math.max(1, list.length)) + 1} of {list.length}</span>
          </div>
          <p style={{ fontSize: 22, fontWeight: 500, lineHeight: 1.35 }}>{sentence}</p>
          <label className="row"><input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} /> The speaker agrees to this recording being stored and used to test recognition.</label>
          <div className="row">
            <button className={`btn ${rec ? "danger" : ""}`} disabled={!consent} onClick={record}>{rec ? "Stop and save" : "Record"}</button>
            <button className="btn quiet" onClick={() => setI(i + 1)}>Skip sentence</button>
          </div>
        </section>
        <section className="panel panel-pad stack">
          <h2>Measure accuracy</h2>
          <p className="muted">{clips.data?.clips.length || 0} clips recorded. Word error rate (lower is better) and how many technical terms survive recognition.</p>
          <div className="row">{available.map((k) => (
            <label key={k} className="pill" style={{ cursor: "pointer" }}><input type="checkbox" checked={engines.includes(k)} onChange={(e) => setEngines(e.target.checked ? [...engines, k] : engines.filter((x) => x !== k))} /> {k}</label>
          ))}</div>
          <button className="btn" disabled={!engines.length || !clips.data?.clips.length || busy} onClick={evaluate}>{busy ? "Measuring…" : "Run evaluation"}</button>
          {busy && <Spinner label="Transcribing every clip with each engine" />}
          {results && (
            <table className="tbl">
              <thead><tr><th>Engine</th><th className="r">WER</th><th className="r">English</th><th className="r">Hinglish</th><th className="r">Terms kept</th><th className="r">p50</th></tr></thead>
              <tbody>{Object.entries(results.results).map(([k, r]: any) => (
                <tr key={k}><td>{k}</td><td className="r num"><b>{(r.wer * 100).toFixed(1)}%</b></td><td className="r num">{r.by_language["en-IN"] != null ? (r.by_language["en-IN"] * 100).toFixed(1) + "%" : "–"}</td>
                  <td className="r num">{r.by_language["hi-en"] != null ? (r.by_language["hi-en"] * 100).toFixed(1) + "%" : "–"}</td><td className="r num">{Math.round(r.term_recall * 100)}%</td><td className="r num">{r.p50_ms} ms</td></tr>
              ))}</tbody>
            </table>
          )}
        </section>
      </div>
      <section className="panel" style={{ marginTop: 18 }}>
        <div className="panel-pad" style={{ paddingBottom: 6 }}><h2>Clips</h2></div>
        <table className="tbl">
          <thead><tr><th>Speaker</th><th>Language</th><th>Sentence</th><th className="r">Length</th><th /></tr></thead>
          <tbody>{clips.data?.clips.map((c: any) => (
            <tr key={c.id}><td>{c.speaker}{c.accent ? <span className="muted">, {c.accent}</span> : null}</td><td>{c.language}</td><td>{c.reference}</td><td className="r num">{c.duration_s}s</td>
              <td className="r"><audio controls preload="none" src={`/api/lab/clips/${c.id}/audio`} style={{ height: 30 }} /> <button className="btn link" onClick={async () => { await del(`/api/lab/clips/${c.id}`); clips.reload(); }}>Delete</button></td></tr>
          ))}</tbody>
        </table>
      </section>
    </>
  );
}
