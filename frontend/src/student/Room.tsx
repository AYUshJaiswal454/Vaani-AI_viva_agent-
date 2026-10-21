import { useEffect, useRef, useState } from "react";
import { get, mmss } from "../api";
import { navigate, Spinner } from "../ui";
import { BrowserSTT, dbfs, type SayMsg, VivaSocket } from "../voice";
import { ensureAudio, shared } from "./shared";

type Phase = { id: string; start: number; end: number };
const PHASE_LABEL: Record<string, string> = { warmup: "Warm-up", fundamentals: "Fundamentals", depth: "Depth", applied: "Applied problem", wrapup: "Wrap-up" };
const SHOWN_KINDS = new Set(["question", "followup", "hint", "rephrase", "repeat", "redirect", "prompt", "wrapup"]);

export function Room() {
  const [boot, setBoot] = useState<"loading" | "needs-tap" | "running" | "done" | "error">("loading");
  const [info, setInfo] = useState<any>(null);
  const [err, setErr] = useState("");
  const [conn, setConn] = useState<string>("connecting");
  const [phases, setPhases] = useState<Phase[]>([]);
  const [phase, setPhase] = useState("warmup");
  const [question, setQuestion] = useState<{ text: string; kind: string; code?: string | null } | null>(null);
  const [speaking, setSpeaking] = useState(false);
  const [listening, setListening] = useState(false);
  const [thinking, setThinking] = useState(false);
  const [caption, setCaption] = useState("");
  const [code, setCode] = useState<string | null>(null);
  const [pad, setPad] = useState("");
  const [warning, setWarning] = useState("");
  const [remaining, setRemaining] = useState(900);
  const [endReason, setEndReason] = useState("");
  const sock = useRef<VivaSocket | null>(null);
  const stt = useRef<BrowserSTT | null>(null);
  const clock = useRef({ elapsed: 0, at: performance.now(), started: false, duration: 900 });
  const canvas = useRef<HTMLCanvasElement>(null);
  const codeQ = useRef<string | null>(null);
  const quietSince = useRef<number | null>(null);
  const padTimer = useRef<number>(0);
  const stateRef = useRef({ speaking: false, listening: false, thinking: false });
  stateRef.current = { speaking, listening, thinking };

  useEffect(() => {
    get("/api/student/session").then((r) => {
      setInfo(r);
      if (["ended", "scoring", "scored", "reviewed", "abandoned"].includes(r.session.status)) { setBoot("done"); return; }
      if (!r.session.consented) { navigate("/viva"); return; }
      if (shared.mic && shared.player) start(r); else setBoot("needs-tap");
    }).catch((e) => { setErr(e.message); setBoot("error"); });
    return () => { sock.current?.close(); stt.current?.stop(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function tapToStart() {
    try { await ensureAudio(); start(info); } catch (e: any) { setErr(`Microphone: ${e?.message || e}`); setBoot("error"); }
  }

  function start(r: any) {
    setBoot("running");
    const { mic, player } = shared as any;
    const s = new VivaSocket(r.session.id);
    sock.current = s;
    if (r.session.settings?.stt_engine === "browser" && BrowserSTT.available()) {
      const b = new BrowserSTT(r.session.settings.language === "hinglish" ? "hi-IN" : "en-IN");
      b.onResult = (text, final) => s.json({ type: "browser_stt", text, final });
      stt.current = b;
    }
    mic.onChunk = (pcm: ArrayBuffer) => s.sendAudio(pcm);
    player.onStart = (utt: string) => {
      s.json({ type: "playback", event: "start", utt, t: Date.now() });
      setSpeaking(true); setListening(false); setThinking(false);
      stt.current?.stop();
    };
    player.onEnd = (utt: string, skipped: boolean) => {
      s.json({ type: "playback", event: skipped ? "skipped" : "end", utt, t: Date.now() });
      setSpeaking(false);
      setListening(true);
      quietSince.current = null;
      stt.current?.start();
    };
    s.onState = (st) => setConn(st);
    s.onMessage = (m) => onMessage(m, s, player);
    s.connect();
  }

  function onMessage(m: any, s: VivaSocket, player: any) {
    switch (m.type) {
      case "hello":
        setPhases(m.phases || []);
        clock.current = { elapsed: m.elapsed || 0, at: performance.now(), started: !!m.resumed, duration: m.duration_s || 900 };
        if (!m.resumed && !s.started) {
          s.started = true;
          s.offset = 0;
          s.json({ type: "start", client: { ua: navigator.userAgent, mic: shared.mic?.label || "" } });
          clock.current.started = true;
        } else {
          s.started = true;
        }
        break;
      case "say": {
        const msg = m as SayMsg;
        if (msg.elapsed !== undefined) clock.current = { ...clock.current, elapsed: msg.elapsed, at: performance.now() };
        if (msg.phase) setPhase(msg.phase);
        const text = msg.segments.map((x) => x.text).join(" ");
        if (msg.expect && SHOWN_KINDS.has(msg.kind) || msg.kind === "close") {
          const shown = msg.segments.filter((x) => x.source.startsWith("bank:") || x.source.startsWith("variant:")).map((x) => x.text).join(" ");
          setQuestion({ text: shown || text, kind: msg.kind, code: msg.code });
        }
        if (msg.show_code) { setCode(msg.show_code); codeQ.current = msg.code || null; }
        else if (msg.kind === "question" && msg.code !== codeQ.current) { setCode(null); codeQ.current = null; }
        setCaption("");
        setThinking(false);
        player.enqueue(msg);
        break;
      }
      case "stop_audio":
        player.stop();
        break;
      case "listening":
        setListening(true); setThinking(false);
        break;
      case "caption":
        setCaption(m.text || "");
        break;
      case "pong":
        if (typeof m.elapsed === "number" && clock.current.started) clock.current = { ...clock.current, elapsed: m.elapsed, at: performance.now() };
        break;
      case "warning":
        setWarning(m.message || "Please check your microphone.");
        window.setTimeout(() => setWarning(""), 9000);
        break;
      case "end":
        setEndReason(m.reason || "completed");
        window.setTimeout(() => {
          sock.current?.close();
          shared.mic?.stop(); shared.mic = null;
          setBoot("done");
        }, player.playing ? 1500 : 200);
        break;
      case "error":
        setErr(m.message || "Something went wrong.");
        break;
    }
  }

  // clock, mic-silence check
  useEffect(() => {
    if (boot !== "running") return;
    const t = window.setInterval(() => {
      const c = clock.current;
      const el = c.started ? c.elapsed + (performance.now() - c.at) / 1000 : 0;
      setRemaining(Math.max(0, c.duration - el));
      const mic = shared.mic;
      if (mic && stateRef.current.listening && !stateRef.current.speaking) {
        if (dbfs(mic.level) < -72 || mic.muted) {
          quietSince.current = quietSince.current ?? performance.now();
          if (performance.now() - quietSince.current > 8000) setWarning("We can't hear anything from your microphone. Check it isn't muted.");
        } else quietSince.current = null;
      }
    }, 250);
    return () => window.clearInterval(t);
  }, [boot]);

  // integrity signals + keyboard
  useEffect(() => {
    if (boot !== "running") return;
    let lastBlur = 0;
    const vis = () => { if (document.hidden) sock.current?.json({ type: "integrity", kind: "tab_hidden" }); };
    const blur = () => { if (Date.now() - lastBlur > 10000) { lastBlur = Date.now(); sock.current?.json({ type: "integrity", kind: "focus_lost" }); } };
    const key = (e: KeyboardEvent) => {
      const typing = (e.target as HTMLElement)?.tagName === "TEXTAREA" || (e.target as HTMLElement)?.tagName === "INPUT";
      if (typing) return;
      if (e.code === "Space") { e.preventDefault(); done(); }
      if (e.key === "r" || e.key === "R") repeat();
    };
    document.addEventListener("visibilitychange", vis);
    window.addEventListener("blur", blur);
    window.addEventListener("keydown", key);
    return () => { document.removeEventListener("visibilitychange", vis); window.removeEventListener("blur", blur); window.removeEventListener("keydown", key); };
  }, [boot]);

  // the voice ring
  useEffect(() => {
    if (boot !== "running") return;
    let raf = 0;
    let angle = 0;
    const draw = () => {
      const c = canvas.current;
      if (c) {
        const dpr = window.devicePixelRatio || 1;
        const size = c.clientWidth;
        if (c.width !== size * dpr) { c.width = size * dpr; c.height = size * dpr; }
        const g = c.getContext("2d")!;
        g.setTransform(dpr, 0, 0, dpr, 0, 0);
        g.clearRect(0, 0, size, size);
        const st = stateRef.current;
        const exLevel = shared.player ? shared.player.level() : 0;
        const micLevel = shared.mic ? shared.mic.level : 0;
        const mode = st.speaking ? "ex" : st.thinking ? "think" : st.listening ? "me" : "idle";
        const color = mode === "ex" ? "#f2c94c" : mode === "me" ? "#8ec5e8" : "#6f7d76";
        const lvl = mode === "ex" ? Math.min(1, exLevel * 5) : mode === "me" ? Math.min(1, micLevel * 9) : 0;
        const n = 72, cx = size / 2, cy = size / 2, r0 = size * 0.3;
        angle += mode === "think" ? 0.03 : 0.002;
        g.lineCap = "round";
        for (let i = 0; i < n; i++) {
          const a = angle + (i / n) * Math.PI * 2;
          const wobble = Math.sin(i * 1.7 + performance.now() / 260) * 0.5 + 0.5;
          const len = size * (0.03 + 0.15 * lvl * (0.45 + 0.55 * wobble)) * (mode === "think" ? (i % 9 < 3 ? 1.8 : 0.6) : 1);
          g.strokeStyle = color;
          g.globalAlpha = mode === "idle" ? 0.35 : 0.55 + 0.45 * wobble;
          g.lineWidth = Math.max(1.5, size * 0.012);
          g.beginPath();
          g.moveTo(cx + Math.cos(a) * r0, cy + Math.sin(a) * r0);
          g.lineTo(cx + Math.cos(a) * (r0 + len), cy + Math.sin(a) * (r0 + len));
          g.stroke();
        }
        g.globalAlpha = 1;
      }
      raf = requestAnimationFrame(draw);
    };
    draw();
    return () => cancelAnimationFrame(raf);
  }, [boot]);

  function done() {
    if (!stateRef.current.listening) return;
    sock.current?.json({ type: "done" });
    setThinking(true);
  }
  function repeat() { sock.current?.json({ type: "repeat" }); }
  function endEarly() {
    if (window.confirm("End your viva now? You won't be able to continue it.")) sock.current?.json({ type: "end_early" });
  }
  function onPad(v: string) {
    setPad(v);
    window.clearTimeout(padTimer.current);
    padTimer.current = window.setTimeout(() => sock.current?.json({ type: "code", text: v }), 700);
  }

  if (boot === "loading") return <div className="board"><div className="done"><Spinner label="Opening your viva" /></div></div>;
  if (boot === "error") return <div className="board"><div className="done"><div><h1>Something went wrong</h1><p>{err}</p></div></div></div>;
  if (boot === "needs-tap") return (
    <div className="board"><div className="done"><div>
      <h1>Ready to continue</h1>
      <p>Your browser needs a tap before it can use the microphone and play sound.</p>
      <p style={{ marginTop: 22 }}><button className="btn" onClick={tapToStart}>Continue my viva</button></p>
    </div></div></div>
  );
  if (boot === "done") return (
    <div className="board"><div className="done"><div>
      <h1>{endReason === "time_up" ? "Time's up. Thank you!" : "Viva complete. Thank you!"}</h1>
      <p>Your answers and the recording have been submitted. Your faculty reviews every score before results are shared. You can close this window.</p>
    </div></div></div>
  );

  const total = phases.length ? phases[phases.length - 1].end : 870;
  const elapsed = (clock.current.duration || 900) - remaining;
  const ringText = speaking ? "Vaani is speaking" : thinking ? "One moment" : listening ? "Your turn" : "Connecting";
  const clockCls = remaining <= 30 ? "final" : remaining <= 120 ? "late" : "";

  return (
    <div className="board">
      <div className="room">
        <header className="room-top">
          <div className="wordmark">vaani<span className="dot">.</span></div>
          <div className="subject">{info?.viva?.subject}</div>
          <div className={`clock ${clockCls}`} aria-live="off"><b className="num">{mmss(remaining)}</b><span>left</span></div>
        </header>
        <section className={`stage ${code ? "with-code" : ""}`}>
          <div className="speaker">
            <div className="ring-wrap">
              <canvas ref={canvas} aria-hidden />
              <div className="ring-state">{ringText}</div>
            </div>
            <p className={`question ${question ? "" : "waiting"}`} aria-live="polite">{question?.text || "Your examiner will greet you in a moment."}</p>
            <div className="qmeta">
              <span>{PHASE_LABEL[phase] || phase}</span>
              {question?.code && <span>Question {question.code}</span>}
            </div>
            <p className="caption" aria-live="polite">{caption}</p>
          </div>
          {code && (
            <aside className="codepanel" aria-label="Code for this problem">
              <h3>Code for this problem</h3>
              <pre><code>{code}</code></pre>
              <h3>Your notes or fixed code (optional)</h3>
              <textarea value={pad} onChange={(e) => onPad(e.target.value)} onPaste={() => sock.current?.json({ type: "integrity", kind: "paste" })}
                placeholder="Type here while you explain out loud…" spellCheck={false} />
            </aside>
          )}
        </section>
        <footer className="room-bottom">
          {conn !== "open" && <p className="banner" role="status">{conn === "failed" ? "Connection lost. Reload the page to continue where you stopped." : "Reconnecting… your viva will continue where it stopped."}</p>}
          {warning && <p className="banner bad" role="alert">{warning}</p>}
          {err && <p className="banner bad" role="alert">{err}</p>}
          <div className="rail" aria-label="Viva progress">
            {phases.map((p) => {
              const fill = Math.max(0, Math.min(1, (elapsed - p.start) / (p.end - p.start)));
              return (
                <div key={p.id} className={`seg ${phase === p.id ? "current" : ""}`} style={{ flex: `${p.end - p.start} 1 0` }}>
                  <i className="fill" style={{ width: `${fill * 100}%` }} />
                  <span>{PHASE_LABEL[p.id]}</span>
                </div>
              );
            })}
            {phases.length > 0 && <i className="now" style={{ left: `${Math.min(100, (elapsed / total) * 100)}%` }} />}
          </div>
          <div className="controls">
            <button className="btn" onClick={done} disabled={!listening}>I'm done <span className="kbd">Space</span></button>
            <button className="btn quiet" onClick={repeat}>Repeat question <span className="kbd">R</span></button>
            <span className="spacer" />
            {info?.session?.settings?.allow_end_early !== false && <button className="btn link" onClick={endEarly}>End viva early</button>}
          </div>
        </footer>
      </div>
    </div>
  );
}
