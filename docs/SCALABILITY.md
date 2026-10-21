# Scalability and capacity plan

How many vivas one Vaani server can run at the same time, what limits it, and how to run 350 students.
Every number here comes from the load tests in `loadtest/` (results in `loadtest/results/`).

## What a live viva costs the server

While a student talks, the server receives 10 audio frames a second and for each one: runs the voice detector
(Silero VAD, about 0.5 ms per 32 ms window), appends the audio to the encrypted recording, and acknowledges every
tenth frame. When an answer ends it runs one step of the examiner state machine (under 10 ms), saves the state and
the transcript line, and sends the reply, whose audio is a pre-synthesised, cached clip. Nothing on this path calls
an AI model unless optional follow-up rewording is on (1.4 s budget, falls back to the bank text).

The expensive part is **speech recognition**. Vaani can do it on the server (Whisper, no API cost) or send it to a
hosted recogniser (Groq, OpenAI, Sarvam). Those are two very different capacity profiles, so both were measured.

## Load test: 50, 100 and 150 concurrent vivas

`loadtest/load.py` starts N audio bots over 60 seconds. Each joins with its own roll number, gives consent and
then holds a real-time conversation for 4 minutes: 10 audio frames a second, synthesised speech for its answers,
the examiner's audio fetched and "played" for its real length. The server does everything it does for a student
(voice detection, encrypted recording, examiner, cached voice, transcript writes). Speech recognition is the stub
engine with a 250 ms delay, standing in for a hosted recogniser, so the numbers measure Vaani itself. The test
machine has **2 vCPU and runs the bots as well as the server**, so the server gets less than the whole machine.

| Server | Concurrent vivas | Replies | Reply p50 | Reply p95 | Dropped | RAM |
|---|---|---|---|---|---|---|
| 1 process, before optimisation | 50 | 625 | 1235 ms | 1332 ms | 0 | 349 MB |
| | 100 | 1076 | 1627 ms | 3026 ms | 0 | 728 MB |
| | 150 | 864 | 3915 ms | 9561 ms | 1 | 1025 MB |
| | 200 | 722 | 5220 ms | 15084 ms | 7 | 1293 MB |
| 1 process, optimised | 100 | 1272 | 1248 ms | **1470 ms** | 0 | 585 MB |
| | 150 | 1406 | 2069 ms | 3586 ms | 1 | 1032 MB |
| 2 processes, optimised | 150 | 1850 | 1284 ms | **1866 ms** | 0 | 987 MB |
| | 200 | 2126 | 1583 ms | 5422 ms | 0 | 1493 MB |
| | 250 | 2278 | 2020 ms | 7432 ms | 6 | 1787 MB |

**Result: 150 concurrent vivas on one 2-vCPU machine** (two web processes) with replies at p50 1.3 s and p95 1.9 s
and nothing dropped. **Breaking point:** at 200 the median is still 1.6 s but the p95 passes 5 s; at 250 a few
connections time out. A single process saturates one core first: it holds the target up to 100 vivas.

What the first run taught us (profiled with `cProfile` at 80 vivas): 23% of the server's CPU went into text
normalisation that recompiled the alias regular expressions for every key-point term of every answer, and about
9% into encrypting and writing each 100 ms audio frame separately. Caching normalised text with precompiled alias
tables and writing the recording in half-second encrypted blocks moved the single-process limit from about 50 to
100 vivas. The largest remaining per-viva cost is the voice-detection model (one ONNX call per 32 ms of speech).

## Local recognition: where the CPU runs out

`loadtest/stt_capacity.py` ran 4, 6 and then 8 audio-bot vivas at once against a 2-vCPU server doing all speech
recognition itself (Whisper base.en, two single-thread recognition workers), each stage for 4 minutes of real
conversation. The bots speak almost continuously when answering, which is heavier than real students, who pause.

| Concurrent vivas | Replies | Reply p50 | Reply p95 | Max | Dropped | Server CPU (avg of 200%) | Server RAM |
|---|---|---|---|---|---|---|---|
| 4 | 52 | 1250 ms | 2206 ms | 3096 ms | 0 | 60% | 1.26 GB |
| 6 | 80 | 1269 ms | 2315 ms | 2884 ms | 0 | 84% | 1.37 GB |
| 8 | 109 | 1305 ms | 2646 ms | 3564 ms | 0 | 107% | 1.38 GB |

**Capacity with local recognition: 6 concurrent vivas on 2 vCPU (3 per vCPU) within the p95 < 2.5 s target.** Past
that, replies slow down gradually rather than failing: at 8 the p95 is 2.6 s and nothing was dropped. Memory is
dominated by the two Whisper instances (about 1.3 GB in total), not by the number of vivas.

## Capacity plan for 350 students

A viva slot is 18 minutes (15-minute viva plus joining, consent and the microphone check).

| Setup | Concurrent vivas | Rounds for 350 | Time for all 350 | Server cost (AWS Mumbai, on demand) |
|---|---|---|---|---|
| One 8-vCPU server, local Whisper (no API) | 24 (3 per vCPU, measured on 2 vCPU) | 15 | 4.5 h | c7i.2xlarge, about $1.6 |
| Two 8-vCPU servers, local Whisper | 48 | 8 | 2.4 h | about $1.7 |
| One 2-vCPU server, hosted recognition (Groq / OpenAI / Sarvam) | 150 (load test, 2 processes) | 3 | 54 min | c7i.large, under $0.10, plus API cost |

Assumptions and other resources:

- **Local recognition scales with cores.** Each recognition worker is an independent single-thread model instance,
  so 8 vCPU run 8 workers; the 3-per-vCPU figure is from the 2-vCPU measurement and should be confirmed on the actual
  server with `loadtest/stt_capacity.py` before the exam day.
- **Network.** The browser sends raw 16 kHz 16-bit audio: 256 kbit/s per student while the viva runs. 24 students
  need about 6 Mbit/s into the server, 150 students about 38 Mbit/s. Examiner audio going out is small (cached,
  compressed clips).
- **Memory.** About 650 MB per local Whisper base.en worker; with hosted recognition the server used under 1 GB
  at 150 concurrent vivas.
- **After each viva**, mixing the recording, integrity analysis and scoring take about 30 s of one core (measured
  on the sample vivas, run at low priority while other vivas were live). 350 vivas need about 3 CPU-hours of this
  in total, spread over the day, or on separate worker processes.
- **Storage.** About 3.5 MB of encrypted audio per viva (both tracks), so 1.2 GB for 350 students, deleted
  automatically after the retention period.
- **Cost.** See `docs/COST.md`: about ₹135 of server time for all 350 students on a campus server with no API at
  all, about ₹230 with hosted open-weight models, about ₹2,300 with OpenAI's premium models.

## Scaling out

- **More processes.** The examiner's state is saved after every answer and every WebSocket frame carries its own
  sequence number and sample offset, so a viva does not depend on staying in one process: a student whose connection
  drops can reconnect to any web process and continue (the examiner repeats the last question). Run several web
  processes behind a load balancer that supports WebSockets, with `DATABASE_URL` pointing at Postgres so they share
  sessions, transcripts and the task queue.
- **Workers apart from the web.** Set `EMBEDDED_WORKER=false` on web processes and run `python -m app.worker`
  (Docker Compose profile `scale`). Recording mix-down, integrity analysis and scoring then never compete with live
  vivas. Even in a single process this work runs on low-priority threads (measured: before this change, finishing one
  viva's recording delayed replies in the other live vivas by up to 17 s).
- **Recognition capacity.** With local Whisper, each recognition worker is one model instance on one CPU core
  (`STT_CONCURRENCY`, default one per core). Add cores, a second server, or switch to a hosted recogniser for
  peaks. `faster-whisper` (CTranslate2) is supported for GPU servers.
- **Failure handling.** If the server restarts mid-viva, the student's browser reconnects with backoff and resends
  the last 6 seconds of audio; the viva clock pauses during the outage (up to 2 minutes in total) and the attempt
  resumes from the saved state. Stale sessions are closed by a sweeper after the grace period and flagged for review.

## Bottlenecks and mitigations

| Bottleneck | Symptom | Mitigation |
|---|---|---|
| Local speech recognition CPU | reply time grows with concurrent speakers | more cores / servers, hosted STT, smaller model (`tiny.en`) for peaks |
| Post-viva processing on the web process | reply stalls when another viva ends | low-priority threads (done); separate worker processes |
| SQLite writes under many processes | lock waits | WAL mode (default); Postgres for multi-server |
| Hosted provider limits (rate, latency) | slow or failed recognition | per-viva engine choice, automatic local fallback, circuit breaker on the LLM |
| Network quality at the student's end | drops, late audio | client resend buffer, sample-offset alignment, paused viva clock, rejoin |
