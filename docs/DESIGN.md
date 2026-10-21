# Vaani: design

Vaani is an AI examiner for 15-minute oral vivas. It asks questions from the faculty's bank by voice, listens,
adapts its follow-ups, never gives answers away, stops on time, scores against the rubric with every mark linked to
what the student said, and keeps the recording and transcript for faculty to review and override.

## 1. Architecture

```
 Student browser                         Vaani server (one Python process, or many behind a load balancer)
 ────────────────                        ─────────────────────────────────────────────────────────────────────
 mic → AudioWorklet (16 kHz PCM)   ──WS──▶ LiveSession ── VAD (Silero) ── streaming STT (Whisper ONNX / API)
   100 ms frames, seq + sample offset      │   recording: encrypted chunk by chunk, aligned to sample offsets
 examiner audio ◀── cached clip URLs ◀──── │   Examiner engine: pure state machine (state, event, clock) → actions
 captions, timer, phase strip ◀── JSON ─── │   TTS phrase cache (bank phrases synthesised when the viva opens)
                                           ▼
                                   SQLite/Postgres: sessions (state after every turn), turns, scores, audit
                                           │
                                   Task queue (DB-backed, priorities) → worker: mix-down → integrity → scoring
                                           ▼
 Faculty browser ◀── REST ─────── review: audio + waveform, transcript, evidence-linked scores, override, export
```

- **One WebSocket per viva.** Audio frames carry a sequence number and the sample offset since the viva started, so
  after a network drop the client resends the last 6 s and the server places every sample at the right time; gaps
  are filled with silence. Recording timestamps therefore match the transcript exactly.
- **Stateless live workers.** The examiner's whole state is saved after every turn. A student who reconnects (to
  the same or another process) resumes where they were; the examiner repeats the last question. The viva clock
  pauses during a drop (up to 2 minutes in total), the recording clock does not.
- **Everything slow happens off the live path.** Examiner phrases are synthesised when the viva opens; the
  mixed recording, integrity analysis and AI scoring run in the task queue after the viva.

## 2. Turn-taking and latency

The target is a reply within about 2 seconds of the student finishing. The budget:

| Step | Time | How |
|---|---|---|
| Detect the end of the answer | 900 ms of silence (2.2 s if the sentence is clearly unfinished, "...because") | Silero VAD behind an adaptive noise gate |
| Speech-to-text | hidden | Speech is recognised **while the student talks**: chunks are cut at natural pauses; at 350 ms of silence the tail is recognised speculatively. If the student carries on, that result becomes a committed chunk instead of being thrown away. |
| Decide | < 10 ms | Rule-based examiner (no LLM on the critical path) |
| Speak | ~0 ms | The reply is a pre-synthesised clip; an optional LLM rewording has a 1.4 s budget and falls back to the bank text |

Measured with an audio bot on 2 vCPU and local Whisper base.en: reply p50 1.2 s, p95 1.5 s (was 2.5 / 4.3 s before
streaming recognition). Details in `docs/TEST_REPORT.md`.

## 3. The examiner

A deterministic state machine, unit-tested without audio. It owns the clock (phases at 1, 5, 10, 13 and 14:30
minutes, hard stop at 15:00, 90 s per answer), the question plan and every decision:

- **Plan.** Each student gets a seeded plan spread across topics (different students get different questions), and
  the questions are snapshotted into the attempt so later bank edits never change an old transcript.
- **Adaptive follow-ups.** Each answer is assessed against the question's key points (term matching with stems,
  aliases, common recognition slips and Hinglish): strong → deeper follow-up; partial → one hint, then move on;
  "I don't know" (English or Hindi) → move on without penalty; off-topic → redirect; misconception → probe it.
  Depth questions go to the student's strongest topic; a struggling student gets easier questions.
- **Never reveals answers.** Hints are checked for key-term leaks when the bank is saved; every non-bank line passes
  an evaluative-language guard ("correct", "exactly"...), and LLM rewordings are rejected if they contain key terms.
- **Traceable.** Every examiner line carries its source: `bank:DBMS-F-03`, `/f1` follow-up, `/h1` hint, `/r`
  rephrase, `variant:` (logged LLM rewording with the original), or `system:...`. The decision log records why.
- **Robust conversation.** Silence ladder (prompt at 10 s, rephrase at 22 s, move on at 35 s), "let me think"
  pauses the prompts, "repeat / what do you mean" handled, barge-in after 600 ms of real speech, mic-silence warning,
  identity check against the roll number, prompt-injection attempts flagged.

## 4. Scoring and audit

Two independent scorers run after the viva:

1. **Rule scorer** (always available, deterministic): key-point coverage per phase, reasoning and depth evidence,
   applied-problem structure, project specifics, clarity.
2. **AI scorer** (when an LLM is configured): temperature 0, three runs, median per dimension. The prompt contains
   the rubric anchors, the reference key points and the transcript with student text wrapped as data; the student's
   name and roll number are redacted. Every reason must cite transcript line ids; citations are validated and
   uncited marks are flagged.

Stored with every score: the prompt version and hashes (`scorer-prompt-1.0#…|rubric-2026.1#…`), model, all runs.
An attempt goes to **mandatory review** when the AI runs disagree by 2+ points, the two scorers disagree (15+ total or
2+ on a dimension), a mark is uncited, speech recognition confidence is low, there were fewer than 4 answers, or
integrity signals or disconnects occurred. Faculty override any dimension with a reason; the override always wins and
the audit log keeps before/after values. Blind faculty marks feed a calibration page (MAE, Pearson, Spearman, bias).

## 5. Speech

Built in, no API needed: Whisper (tiny/base/small, English or multilingual) exported to ONNX and run with
onnxruntime and numpy (our own log-mel front end and BPE tokenizer), with the question text and key terms as a short
prompt for technical vocabulary, per-token log-probabilities for a confidence score, and a pool of single-thread
workers. Hosted engines (Groq Whisper large-v3-turbo, OpenAI, Sarvam for Hinglish) and the browser's recogniser are
one setting away. Examiner voice: the browser, flite or Piper offline, or OpenAI / Sarvam voices.

## 6. Scaling to 350 students

Live work per viva is light: VAD on 100 ms frames, encrypted appends, a state-machine step per answer, cached clips.
With hosted speech recognition one 2-vCPU server ran **150 concurrent vivas** in the load test; the limit with local
Whisper is CPU (see `docs/SCALABILITY.md`), so a college server either uses a hosted recogniser or adds recognition
capacity per concurrent student. Horizontal scaling: several web processes share Postgres (sticky WebSockets are not
required because state is persisted per turn) and workers scale independently. Cost for 350 students:
`docs/COST.md`.

## 7. Privacy and fairness

Consent screen before the microphone opens; recordings encrypted at rest; role-based access, signed audio links and
an audit trail; automatic deletion after the retention period and admin erasure. Accent, grammar and language choice
are not marked (rubric, prompt and tests). AI marks are recommendations until faculty accept or override them.
See `docs/PRIVACY.md`.
