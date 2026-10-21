// Browser side of the live viva: microphone capture, the WebSocket with resend-after-drop, examiner playback
// (server audio through Web Audio, or the browser's own voice), and optional browser speech recognition.

export type SayMsg = {
  type: "say"; utt: string; kind: string; segments: { text: string; source: string; audio?: string | null; duration?: number | null }[];
  more: boolean; expect: boolean; phase?: string; code?: string | null; show_code?: string | null; elapsed?: number; resumed?: boolean; tts?: string;
};

// ------------------------------------------------------------------------------------------- microphone
export class Mic {
  ctx: AudioContext | null = null;
  stream: MediaStream | null = null;
  node: AudioWorkletNode | null = null;
  level = 0;                       // smoothed RMS (0..1)
  onChunk: ((pcm: ArrayBuffer, rms: number) => void) | null = null;
  muted = false;

  async start(): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    });
    let ctx: AudioContext;
    try { ctx = new AudioContext({ sampleRate: 16000 }); } catch { ctx = new AudioContext(); }
    this.ctx = ctx;
    await ctx.audioWorklet.addModule("/assets/mic-worklet.js");
    const src = ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(ctx, "mic-processor");
    this.node.port.onmessage = (e) => {
      const { pcm, rms } = e.data;
      this.level = this.level * 0.6 + rms * 0.4;
      this.onChunk?.(pcm, rms);
    };
    src.connect(this.node);
    const sink = ctx.createGain();
    sink.gain.value = 0;
    this.node.connect(sink).connect(ctx.destination);      // keeps the graph running without echoing the mic
    const track = this.stream.getAudioTracks()[0];
    track.onmute = () => { this.muted = true; };
    track.onunmute = () => { this.muted = false; };
  }

  get label(): string { return this.stream?.getAudioTracks()[0]?.label || "Microphone"; }

  stop() {
    this.stream?.getTracks().forEach((t) => t.stop());
    this.ctx?.close();
    this.ctx = null;
  }
}

export function dbfs(rms: number) { return 20 * Math.log10(Math.max(rms, 1e-6)); }

// ------------------------------------------------------------------------------------------- playback
export class Player {
  ctx: AudioContext;
  analyser: AnalyserNode;
  current: AudioBufferSourceNode | null = null;
  queue: { msg: SayMsg; seg: SayMsg["segments"][number] }[] = [];
  playing = false;
  pendingMore = false;
  onStart: ((utt: string) => void) | null = null;
  onEnd: ((utt: string, skipped: boolean) => void) | null = null;
  voice: SpeechSynthesisVoice | null = null;
  synthLevel = 0;
  private cache = new Map<string, Promise<AudioBuffer>>();
  private startedUtts = new Set<string>();
  private lastUtt = "";

  constructor() {
    this.ctx = new AudioContext();
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 512;
    this.analyser.connect(this.ctx.destination);
    if ("speechSynthesis" in window) {
      const pick = () => { this.voice = pickVoice(); };
      pick();
      window.speechSynthesis.onvoiceschanged = pick;
    }
  }

  async unlock() { if (this.ctx.state !== "running") await this.ctx.resume(); }

  load(url: string): Promise<AudioBuffer> {
    if (!this.cache.has(url)) {
      this.cache.set(url, fetch(url, { credentials: "same-origin" }).then((r) => {
        if (!r.ok) throw new Error(`audio ${r.status}`);
        return r.arrayBuffer();
      }).then((b) => this.ctx.decodeAudioData(b)));
    }
    return this.cache.get(url)!;
  }

  enqueue(msg: SayMsg) {
    for (const seg of msg.segments) {
      if (seg.audio) this.load(seg.audio).catch(() => undefined);     // start downloading at once
      this.queue.push({ msg, seg });
    }
    this.pendingMore = msg.more;
    if (!this.playing) this.next();
  }

  level(): number {
    if (this.current) {
      const a = new Uint8Array(this.analyser.fftSize);
      this.analyser.getByteTimeDomainData(a);
      let s = 0;
      for (const v of a) { const x = (v - 128) / 128; s += x * x; }
      return Math.sqrt(s / a.length);
    }
    return this.synthLevel;
  }

  private async next() {
    const item = this.queue.shift();
    if (!item) {
      this.playing = false;
      if (!this.pendingMore && this.lastUtt) this.onEnd?.(this.lastUtt, false);
      return;
    }
    this.playing = true;
    const { msg, seg } = item;
    this.lastUtt = msg.utt;
    const markStart = () => {
      if (!this.startedUtts.has(msg.utt)) { this.startedUtts.add(msg.utt); this.onStart?.(msg.utt); }
    };
    if (seg.audio) {
      try {
        const buf = await this.load(seg.audio);
        if (!this.playing) return;
        const src = this.ctx.createBufferSource();
        src.buffer = buf;
        src.connect(this.analyser);
        this.current = src;
        src.onended = () => { if (this.current === src) { this.current = null; this.next(); } };
        markStart();
        src.start();
        return;
      } catch {
        /* fall through to the browser voice */
      }
    }
    if ("speechSynthesis" in window) {
      const u = new SpeechSynthesisUtterance(seg.text);
      if (this.voice) u.voice = this.voice;
      u.lang = this.voice?.lang || "en-IN";
      u.rate = 0.97;
      let t: number | undefined;
      u.onstart = () => {
        markStart();
        t = window.setInterval(() => { this.synthLevel = 0.06 + Math.random() * 0.1; }, 90);
      };
      const done = () => { window.clearInterval(t); this.synthLevel = 0; this.next(); };
      u.onend = done;
      u.onerror = done;
      window.speechSynthesis.speak(u);
    } else {
      markStart();
      globalThis.setTimeout(() => this.next(), Math.max(800, seg.text.length * 65));
    }
  }

  stop() {
    this.queue = [];
    this.pendingMore = false;
    const had = this.playing;
    this.playing = false;
    try { this.current?.stop(); } catch { /* already stopped */ }
    this.current = null;
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    this.synthLevel = 0;
    if (had && this.lastUtt) this.onEnd?.(this.lastUtt, true);
  }
}

export function pickVoice(): SpeechSynthesisVoice | null {
  const vs = window.speechSynthesis?.getVoices() || [];
  const score = (v: SpeechSynthesisVoice) =>
    (v.lang === "en-IN" ? 40 : v.lang.startsWith("en") ? 10 : -50) + (/natural|neural|online/i.test(v.name) ? 30 : 0) +
    (/neerja|heera|veena|rishi|prabhat|swara/i.test(v.name) ? 20 : 0) + (/google/i.test(v.name) ? 8 : 0) + (v.localService ? 0 : 2);
  return vs.slice().sort((a, b) => score(b) - score(a))[0] || null;
}

// ------------------------------------------------------------------------------------------- socket
export class VivaSocket {
  ws: WebSocket | null = null;
  url: string;
  seq = 0;
  offset = 0;                        // samples since the viva started
  outbox: { seq: number; frame: ArrayBuffer }[] = [];
  acked = -1;
  onMessage: ((m: any) => void) | null = null;
  onState: ((s: "connecting" | "open" | "closed" | "failed") => void) | null = null;
  closedByUs = false;
  retries = 0;
  started = false;
  private pingTimer: number | undefined;
  rtt = 0;

  constructor(sessionId: number) {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    this.url = `${proto}://${location.host}/ws/session/${sessionId}`;
  }

  connect() {
    this.onState?.("connecting");
    const ws = new WebSocket(this.url);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onopen = () => {
      this.retries = 0;
      this.onState?.("open");
      window.clearInterval(this.pingTimer);
      this.pingTimer = window.setInterval(() => this.json({ type: "ping", t: Date.now(), rtt: this.rtt }), 4000);
    };
    ws.onmessage = (e) => {
      if (typeof e.data !== "string") return;
      const m = JSON.parse(e.data);
      if (m.type === "hello") {
        this.acked = m.last_seq ?? -1;
        if (m.resumed && typeof m.rec_samples === "number") this.offset = Math.max(this.offset, m.rec_samples);
        for (const f of this.outbox) if (f.seq > this.acked) ws.send(f.frame);       // resend what the server missed
      }
      if (m.type === "ack" || m.type === "pong") {
        if (typeof m.ack === "number" || typeof m.seq === "number") this.acked = Math.max(this.acked, m.ack ?? m.seq);
        this.outbox = this.outbox.filter((f) => f.seq > this.acked);
        if (m.type === "pong" && m.t) this.rtt = Date.now() - m.t;
      }
      this.onMessage?.(m);
    };
    ws.onclose = (e) => {
      window.clearInterval(this.pingTimer);
      if (this.closedByUs || e.code === 4403 || e.code === 4401 || e.code === 4000) { this.onState?.("closed"); return; }
      this.onState?.("connecting");
      const wait = Math.min(8000, 500 * 2 ** this.retries++);
      if (this.retries > 12) { this.onState?.("failed"); return; }
      window.setTimeout(() => this.connect(), wait);
    };
  }

  json(m: any) { if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(m)); }

  sendAudio(pcm: ArrayBuffer) {
    const n = pcm.byteLength / 2;
    const frame = new ArrayBuffer(9 + pcm.byteLength);
    const v = new DataView(frame);
    v.setUint8(0, 1);
    v.setUint32(1, ++this.seq);
    v.setUint32(5, this.started ? this.offset : 0);
    new Uint8Array(frame, 9).set(new Uint8Array(pcm));
    if (this.started) this.offset += n;
    this.outbox.push({ seq: this.seq, frame });
    if (this.outbox.length > 60) this.outbox.splice(0, this.outbox.length - 60);    // keep the last 6 s for resend
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(frame);
  }

  close() { this.closedByUs = true; window.clearInterval(this.pingTimer); this.ws?.close(); }
}

// ------------------------------------------------------------------------------------------- browser STT
export class BrowserSTT {
  rec: any = null;
  active = false;
  onResult: ((text: string, final: boolean) => void) | null = null;
  lang: string;
  constructor(lang = "en-IN") { this.lang = lang; }
  static available() { return !!((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition); }
  start() {
    if (this.active) return;
    const R = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!R) return;
    const rec = new R();
    rec.lang = this.lang;
    rec.continuous = true;
    rec.interimResults = true;
    rec.onresult = (e: any) => {
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        this.onResult?.(r[0].transcript, r.isFinal);
      }
    };
    rec.onend = () => { if (this.active) { try { rec.start(); } catch { /* restarting too fast */ } } };
    rec.onerror = () => undefined;
    this.rec = rec;
    this.active = true;
    try { rec.start(); } catch { /* already started */ }
  }
  stop() { this.active = false; try { this.rec?.stop(); } catch { /* not started */ } }
}
