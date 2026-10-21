import { useEffect, useRef, useState } from "react";
import { get, post } from "../api";
import { navigate, Spinner, useAsync } from "../ui";
import { BrowserSTT, dbfs } from "../voice";
import { ensureAudio, shared } from "./shared";

export function Prepare() {
  const { data, error, loading } = useAsync(() => get("/api/student/session"), []);
  const [agree, setAgree] = useState(false);
  const [micState, setMicState] = useState<"idle" | "asking" | "ok" | "denied">("idle");
  const [level, setLevel] = useState(0);
  const [peak, setPeak] = useState(-90);
  const [heard, setHeard] = useState<"" | "yes" | "no">("");
  const [sttText, setSttText] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const raf = useRef<number>(0);

  useEffect(() => () => cancelAnimationFrame(raf.current), []);
  useEffect(() => {
    if (data?.session?.status && ["ended", "scoring", "scored", "reviewed"].includes(data.session.status)) navigate("/viva/room");
  }, [data]);

  async function checkMic() {
    setErr("");
    setMicState("asking");
    try {
      const { mic } = await ensureAudio();
      setMicState("ok");
      const tick = () => {
        setLevel(mic.level);
        setPeak((p) => Math.max(p - 0.4, dbfs(mic.level)));
        raf.current = requestAnimationFrame(tick);
      };
      tick();
      if (data?.session?.settings?.stt_engine === "browser" && BrowserSTT.available()) {
        const s = new BrowserSTT(data.session.settings.language === "hinglish" ? "hi-IN" : "en-IN");
        s.onResult = (t) => setSttText(t);
        s.start();
        window.setTimeout(() => s.stop(), 12000);
      }
    } catch (e: any) {
      setMicState("denied");
      setErr(e?.name === "NotAllowedError" ? "Microphone permission was blocked. Allow it in the address bar and try again."
        : `Could not open the microphone (${e?.message || e}).`);
    }
  }

  async function playSample() {
    const { player } = await ensureAudio();
    const s = await get("/api/student/voice-sample");
    player.enqueue({ type: "say", utt: "sample", kind: "sample", segments: [{ text: s.text, source: "sample", audio: s.audio }], more: false, expect: false });
  }

  async function start() {
    setBusy(true);
    setErr("");
    try {
      if (!data.session.consented) await post("/api/student/consent", { accept: true, client: { mic: shared.mic?.label || "", ua: navigator.userAgent } });
      navigate("/viva/room");
    } catch (e: any) {
      setErr(e.message);
      setBusy(false);
    }
  }

  if (loading) return <div className="board"><div className="done"><Spinner label="Loading your viva" /></div></div>;
  if (error) return (
    <div className="board"><div className="done"><div><h1>We couldn't find your viva</h1><p>{error}</p>
      <p style={{ marginTop: 18 }}><button className="btn" onClick={() => navigate("/")}>Join again</button></p></div></div></div>
  );

  const micOk = micState === "ok" && peak > -50;
  // This viva relies on the browser's own speech recognition (no server recogniser configured): Chrome or Edge only.
  const needsBrowserStt = data.session.settings?.stt_engine === "browser" && !BrowserSTT.available();
  const ready = agree && micState === "ok" && heard === "yes";
  const resumed = data.session.started && data.session.status === "live";

  return (
    <div className="board">
      <div className="prep">
        <header>
          <div className="wordmark">vaani<span className="dot">.</span></div>
          <div className="sub"><span>{data.student.name}</span> <span style={{ marginLeft: 10 }}>{data.student.roll_no}</span></div>
        </header>
        <div>
          <h1>{data.viva.title}</h1>
          <p className="sub">{data.viva.subject}. At most 15 minutes, by voice.</p>
        </div>
        {resumed && <p className="banner">Your viva is already in progress. Check your microphone, then continue where you left off.</p>}
        <section>
          <h2 style={{ fontSize: 20, marginBottom: 12 }}>Before you start</h2>
          <ul className="rules">{data.rules.map((r: string) => <li key={r}>{r}</li>)}</ul>
        </section>
        <section className="consent">
          <p>{data.consent_text}</p>
          <label>
            <input type="checkbox" checked={agree || data.session.consented} disabled={data.session.consented}
              onChange={(e) => setAgree(e.target.checked)} />
            <span>I agree to my viva being recorded and scored as described above.</span>
          </label>
        </section>
        <section aria-label="Equipment check">
          <div className="check-row">
            <span className="label">Microphone</span>
            {micState === "ok" ? (
              <div>
                <div className="meter" aria-hidden><i style={{ width: `${Math.min(100, level * 900)}%` }} /></div>
                <div className={`state ${micOk ? "good" : ""}`} style={{ marginTop: 6 }}>
                  {micOk ? "We can hear you." : "Say “testing, one two three” out loud."}
                  {sttText && <> Heard: “{sttText}”</>}
                </div>
              </div>
            ) : <span className={`state ${micState === "denied" ? "bad" : ""}`}>{micState === "denied" ? "Blocked" : "Not checked yet"}</span>}
            <button className="btn quiet" onClick={checkMic} disabled={micState === "asking"}>
              {micState === "ok" ? "Recheck" : micState === "asking" ? "Allow access…" : "Check microphone"}
            </button>
          </div>
          <div className="check-row">
            <span className="label">Speakers</span>
            <span className={`state ${heard === "yes" ? "good" : heard === "no" ? "bad" : ""}`}>
              {heard === "yes" ? "You can hear the examiner." : heard === "no" ? "Turn up the volume or plug in headphones, then play it again." : "Play a short sample of the examiner's voice."}
            </span>
            <div className="row">
              <button className="btn quiet" onClick={playSample}>Play sample</button>
              <button className="btn quiet" onClick={() => setHeard("yes")} aria-pressed={heard === "yes"}>I heard it</button>
              <button className="btn link" onClick={() => setHeard("no")}>No sound</button>
            </div>
          </div>
        </section>
        {needsBrowserStt && <p className="banner bad" role="alert">This viva uses your browser's speech recognition, which works in Google Chrome and Microsoft Edge. Please open this page in Chrome or Edge to take the viva.</p>}
        {err && <p className="banner bad" role="alert">{err}</p>}
        <div className="row">
          <button className="btn" disabled={needsBrowserStt || !(ready || (data.session.consented && micState === "ok" && heard === "yes")) || busy} onClick={start}>
            {resumed ? "Continue my viva" : "Start my viva"}
          </button>
          {!ready && <span className="sub">{!agree && !data.session.consented ? "Tick the consent box. " : ""}{micState !== "ok" ? "Check your microphone. " : ""}{heard !== "yes" ? "Confirm you can hear the sample." : ""}</span>}
        </div>
      </div>
    </div>
  );
}
