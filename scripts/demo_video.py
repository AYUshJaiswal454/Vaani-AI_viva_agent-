"""Record the demo video: a student taking a viva by voice, then the faculty review, with real sound.

A headless Chromium drives the real app. The student's microphone is a Web Audio stream that this script fills
with synthesised answers (the answer to whatever question the examiner actually asked, from the "strong" persona),
each played the moment the examiner finishes speaking. The examiner's voice is the server's own audio. Every sound is
logged with its time, so the final MP4 has the conversation exactly as it happened, plus the sample recording that
plays in the review screen.

    # a server with the server-side voice, the samples and an admin password you know:
    TTS_ENGINE=flite STT_ENGINE=whisper-onnx uvicorn app.main:app --port 8014 --ws wsproto
    python scripts/demo_video.py --base http://127.0.0.1:8014 --out docs/demo/vaani-demo.mp4

Needs: pip install playwright (Chromium), ffmpeg.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from loadtest.bot import synth  # noqa: E402
from tests.personas import PERSONAS  # noqa: E402

SR = 16000
W, H = 1280, 720

MIC_SHIM = """
(() => {
  const ctx = new AudioContext({ sampleRate: 48000 });
  const dest = ctx.createMediaStreamDestination();
  const hum = ctx.createConstantSource(); hum.offset.value = 0; hum.connect(dest); hum.start();
  window.__demoSpeak = async (url) => {
    const buf = await (await fetch(url)).arrayBuffer();
    const audio = await ctx.decodeAudioData(buf);
    const src = ctx.createBufferSource(); src.buffer = audio; src.connect(dest); src.start();
    return audio.duration;
  };
  const orig = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  navigator.mediaDevices.getUserMedia = async (c) => (c && c.audio) ? (ctx.resume(), dest.stream.clone()) : orig(c);
})();
"""

CARD = """<html><head><style>
body{margin:0;width:%dpx;height:%dpx;display:flex;align-items:center;justify-content:center;background:#1f2a24;
font-family:Mukta,system-ui,sans-serif;color:#eef2e6}
.c{max-width:900px} h1{font-size:64px;margin:0 0 8px;font-weight:700} p{font-size:28px;line-height:1.45;color:#cdd6c4;margin:6px 0}
small{font-size:20px;color:#9fb09a}
</style></head><body><div class="c">%s</div></body></html>"""


def wav_file(path: Path, x: np.ndarray, sr: int = SR) -> None:
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def decode(data: bytes) -> np.ndarray:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-ac", "1", "-ar", str(SR), "-f", "s16le", "pipe:1"],
                       input=data, capture_output=True, check=True)
    return np.frombuffer(p.stdout, "<i2").astype(np.float32) / 32768


class Recorder:
    def __init__(self) -> None:
        self.t0 = 0.0
        self.sounds: list[tuple[float, np.ndarray]] = []

    def now(self) -> float:
        return time.monotonic() - self.t0

    def add(self, at: float, x: np.ndarray, gain: float = 1.0) -> None:
        self.sounds.append((at, x * gain))

    def track(self, length_s: float) -> np.ndarray:
        out = np.zeros(int(length_s * SR) + SR, np.float32)
        for at, x in self.sounds:
            s = max(0, int(at * SR))
            n = min(len(x), len(out) - s)
            if n > 0:
                out[s:s + n] += x[:n]
        peak = float(np.max(np.abs(out))) or 1.0
        return out / max(1.0, peak / 0.95)


async def card(page, html: str, seconds: float) -> None:
    await page.set_content(CARD % (W, H, html))
    await page.wait_for_timeout(int(seconds * 1000))


async def main_async(args) -> None:
    from playwright.async_api import async_playwright

    import httpx

    bank = {q["code"]: q for q in json.loads((ROOT / "app" / "data" / "banks" / "dbms.json").read_text())["questions"]}
    persona = PERSONAS["strong"](seed=5)
    persona.student_name, persona.roll_no = args.name, args.roll
    tmp = Path(tempfile.mkdtemp(prefix="vaani-demo-"))
    rec = Recorder()
    answers: dict[str, Path] = {}
    events: list[dict] = []

    async with httpx.AsyncClient(base_url=args.base, timeout=30) as http:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
            ctx = await browser.new_context(viewport={"width": W, "height": H}, record_video_dir=str(tmp),
                                            record_video_size={"width": W, "height": H})
            await ctx.add_init_script(MIC_SHIM)

            async def serve_answer(route):
                await route.fulfill(path=str(answers[route.request.url.rsplit("/", 1)[-1]]), content_type="audio/wav")

            await ctx.route("**/__demo/*.wav", serve_answer)
            page = await ctx.new_page()
            rec.t0 = time.monotonic()

            # ---- examiner audio and turn-taking, read from the real WebSocket traffic
            pending: list[dict] = []

            def on_ws(ws):
                def recv(payload):
                    if isinstance(payload, str) and '"type": "say"' in payload or isinstance(payload, str) and '"type":"say"' in payload:
                        m = json.loads(payload)
                        pending.append({"t": rec.now(), "msg": m})
                        events.append({"t": rec.now(), "kind": m.get("kind"), "code": m.get("code")})

                def sent(payload):
                    if isinstance(payload, str) and '"playback"' in payload:
                        m = json.loads(payload)
                        events.append({"t": rec.now(), "playback": m.get("event"), "utt": m.get("utt")})
                ws.on("framereceived", recv)
                ws.on("framesent", sent)
            page.on("websocket", on_ws)

            await card(page, "<h1>Vaani</h1><p>An AI examiner for 15-minute oral vivas.</p><p>It asks from the faculty's question "
                             "bank, listens, follows up, never gives answers away, and scores with evidence.</p>"
                             "<small>Real run: local Whisper speech recognition, server voice, no API keys.</small>", 5)

            # ---- student: join, check, consent
            await page.goto(args.base + "/")
            await page.wait_for_timeout(800)
            await page.fill("input.code-input", args.code)
            await page.fill("input[placeholder='22CS1047']", args.roll)
            await page.fill("input[placeholder='As on your ID card']", args.name)
            await page.wait_for_timeout(600)
            await page.click("button:has-text('Continue')")
            await page.wait_for_url("**/viva")
            await page.wait_for_selector("text=Before you start")
            await page.wait_for_timeout(1500)
            await page.click("button:has-text('Check microphone')")
            await page.wait_for_timeout(1200)
            await page.click("button:has-text('Play sample')")
            await page.wait_for_timeout(1000)
            await page.click("button:has-text('I heard it')")
            await page.check("input[type=checkbox]")
            await page.wait_for_timeout(1500)
            await page.click("button:has-text('Start my viva')")
            await page.wait_for_url("**/viva/room")

            # ---- the viva itself: answer each examiner turn as soon as it finishes
            answered = 0
            last_code = None
            deadline = time.monotonic() + args.student_seconds
            seen = 0
            while time.monotonic() < deadline and answered < args.answers:
                await page.wait_for_timeout(100)
                while seen < len(pending):
                    item = pending[seen]
                    seen += 1
                    m = item["msg"]
                    t_say = item["t"]
                    for seg in m.get("segments", []):          # place the examiner's clips on the sound track
                        if seg.get("audio"):
                            r = await http.get(seg["audio"], cookies=dict((c["name"], c["value"]) for c in await ctx.cookies()))
                            if r.status_code == 200:
                                x = decode(r.content)
                                rec.add(t_say + 0.15, x, 0.9)
                                t_say += len(x) / SR + 0.15
                    if not m.get("expect"):
                        continue
                    # wait for the browser to report the end of playback for this utterance
                    utt = m.get("utt")
                    for _ in range(600):
                        if any(e.get("utt") == utt and e.get("playback") in ("end", "skipped") for e in events):
                            break
                        await page.wait_for_timeout(100)
                    say = {"kind": m.get("kind"), "segments": m["segments"], "code": m.get("code")}
                    q = bank.get(m.get("code") or "") or (bank.get(last_code) if m.get("kind") in ("followup", "hint") else None)
                    resp = persona.respond(say, q if m.get("kind") != "wrapup" else None)
                    if not resp:
                        continue
                    text = resp[0]
                    words = text.split()
                    text = " ".join(words[:45])                  # keep the demo answers short
                    x = synth(text, "slt", 1.05)
                    name = f"a{answered}.wav"
                    wav_file(tmp / name, x)
                    answers[name] = tmp / name
                    await page.wait_for_timeout(700)
                    t_ans = rec.now()
                    await page.evaluate(f"window.__demoSpeak('/__demo/{name}')")
                    rec.add(t_ans + 0.05, x, 1.0)
                    answered += 1
                    last_code = m.get("code") or last_code
            await page.wait_for_timeout(int(args.tail_seconds * 1000))

            # ---- faculty review of a recorded sample viva
            await card(page, "<h1>After the viva</h1><p>Faculty review: the recording with a synced transcript, every mark linked to "
                             "what the student said, override with a reason, and a full audit trail.</p>", 4)
            await page.goto(args.base + "/login")
            await page.fill("input[type=email]", args.faculty_email)
            await page.fill("input[type=password]", args.faculty_password)
            await page.click("button:has-text('Sign in')")
            await page.wait_for_selector("text=Vivas")
            await page.wait_for_timeout(1500)
            await page.click(f"text={args.viva_title}")
            await page.wait_for_timeout(2500)
            await page.click(f"td >> text={args.sample_student}")
            await page.wait_for_selector("audio", state="attached")
            await page.wait_for_timeout(2500)
            sample = await page.evaluate("document.querySelector('audio').src")
            start_at = args.sample_from
            await page.evaluate(f"(() => {{ const a = document.querySelector('audio'); a.currentTime = {start_at}; a.play(); }})()")
            t_play = rec.now()
            r = await http.get(sample, cookies=dict((c["name"], c["value"]) for c in await ctx.cookies()))
            conv = decode(r.content) if r.status_code == 200 else np.zeros(1, np.float32)
            seg = conv[int(start_at * SR): int((start_at + args.listen_seconds) * SR)]
            rec.add(t_play + 0.1, seg, 1.0)
            await page.wait_for_timeout(int(args.listen_seconds * 1000))
            await page.evaluate("document.querySelector('audio').pause()")
            ev = page.locator(".chips button.chip").first
            if await ev.count():
                await ev.click()
                await page.wait_for_timeout(2500)
            ovr = page.locator("section.override").first
            if await ovr.count():                              # change one mark, with a reason (red pen), and save
                await ovr.scroll_into_view_if_needed()
                await page.wait_for_timeout(1200)
                sel = ovr.locator("select").nth(4)
                cur = int(await sel.input_value())
                await sel.select_option(str(max(0, cur - 1) if cur >= 4 else cur + 1))
                await page.wait_for_timeout(800)
                await ovr.locator("textarea").type("Answers were clear but the applied problem explanation skipped the fix; adjusted communication.", delay=18)
                await page.wait_for_timeout(600)
                await ovr.locator("button:has-text('Save changed marks')").click()
                await page.wait_for_timeout(2500)
                await page.evaluate("window.scrollTo(0, 0)")
                await page.wait_for_timeout(2500)
            await page.mouse.wheel(0, 600)
            await page.wait_for_timeout(2000)
            aud = page.locator("summary:has-text('Audit trail')").first
            if await aud.count():
                await aud.scroll_into_view_if_needed()
                await aud.click()
                await page.wait_for_timeout(3000)
            for path, secs in (("/faculty/banks", 4), ("/faculty/calibration", 4), ("/faculty/system", 4)):
                await page.goto(args.base + path)
                await page.wait_for_timeout(int(secs * 1000))
            await card(page, "<h1>Vaani</h1><p>Open source, runs on a college server without paid APIs, or with hosted speech "
                             "and AI when keys are added.</p><small>README, design, test report, load test and cost sheet in the repository.</small>", 5)
            total = rec.now()
            video_path = await page.video.path()
            await ctx.close()
            await browser.close()

    audio = tmp / "track.wav"
    wav_file(audio, rec.track(total))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video_path), "-i", str(audio),
                    "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "medium", "-crf", "30", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "64k", "-shortest", "-movflags", "+faststart", str(out)], check=True)
    (tmp / "events.json").write_text(json.dumps(events, indent=1))
    print("video:", out, f"{out.stat().st_size / 1e6:.1f} MB, {total:.0f} s; working files in {tmp}")
    if not args.keep:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8014")
    ap.add_argument("--code", default="DBMS26")
    ap.add_argument("--roll", default="23CS9001")
    ap.add_argument("--name", default="Kavya Reddy")
    ap.add_argument("--answers", type=int, default=4)
    ap.add_argument("--student-seconds", type=float, default=150)
    ap.add_argument("--tail-seconds", type=float, default=6)
    ap.add_argument("--faculty-email", default="faculty@vaani.local")
    ap.add_argument("--faculty-password", default="faculty123")
    ap.add_argument("--viva-title", default="DBMS mid-semester viva")
    ap.add_argument("--sample-student", default="Ananya Krishnan")
    ap.add_argument("--sample-from", type=float, default=95)
    ap.add_argument("--listen-seconds", type=float, default=22)
    ap.add_argument("--out", default=str(ROOT / "docs" / "demo" / "vaani-demo.mp4"))
    ap.add_argument("--keep", action="store_true")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
