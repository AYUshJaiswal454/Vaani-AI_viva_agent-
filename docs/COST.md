# Cost sheet: 350 students, one 15-minute viva each

Generated 2026-10-21 by `scripts/cost_sheet.py` from the 5 recorded sample vivas and the load-test results. Prices: `app/data/pricing.json` (sources and dates inside); servers: AWS Mumbai on-demand.

## Measured usage per viva

| Quantity | Average | Used for |
|---|---|---|
| Viva length | 10.2 min | server time |
| Student speech | 4.3 min | speech-to-text minutes |
| Answers / follow-ups | 38 / 9 | live LLM calls (only if follow-up rewording is on) |
| Examiner text | 4734 chars, 142 unique to the student | text-to-speech (bank phrases are cached per viva) |
| Scoring prompt | 7374 tokens in, ~900 out, x3 runs | scorer LLM |
| Stored audio | 3.5 MB | storage (1.2 GB for 350 students) |

## Four ways to run it

| Setup | Speech-to-text | Voice | Scoring | API cost / viva | Server | Total for 350 | Per student |
|---|---|---|---|---|---|---|---|
| **Campus server, no paid APIs** | Whisper base.en on the server | Piper / flite on the server (or the browser) | rule scorer + faculty review (optional local LLM via Ollama) | $0.0000 | c7i.2xlarge x 13.2 h (8 at a time) | **$4.71 (₹396)** | ₹1.13 |
| **Hosted APIs, lowest cost** | Groq whisper-large-v3-turbo | browser voice (free) | gpt-oss-120b x3 runs (median) on Groq; gpt-oss-20b for live follow-up wording | $0.0082 | c7i.large x 1.2 h (100 at a time) | **$2.97 (₹250)** | ₹0.71 |
| **Hosted APIs, premium** | OpenAI gpt-4o-mini-transcribe | OpenAI gpt-4o-mini-tts (bank phrases cached) | gpt-6.1-sol x3 runs; gpt-6-luna for live follow-ups | $0.0871 | c7i.large x 1.2 h (100 at a time) | **$30.87 (₹2,593)** | ₹7.41 |
| **Indian-language speech** | Sarvam saaras:v4 (Hinglish, Indian English) | Sarvam bulbul:v2 | gpt-oss-120b x3 runs on Groq | $0.0356 | c7i.large x 1.2 h (100 at a time) | **$13.12 (₹1,102)** | ₹3.15 |

Notes:

- **Campus server, no paid APIs**: Audio never leaves the campus. Throughput limited by CPU speech recognition.
- **Hosted APIs, lowest cost**: Best price/quality. Audio is sent to Groq (US).
- **Hosted APIs, premium**: Most natural voice and strongest scorer.
- **Indian-language speech**: Indian data residency; best for Hinglish answers.

## How the numbers are built

- Server time: 350 students in rounds of N concurrent vivas, 18 minutes per round (viva + joining). N comes from the load tests: 100 concurrent vivas on 2 vCPU with hosted speech recognition, 1.0 per vCPU with local Whisper base.en (see docs/SCALABILITY.md).
- Speech-to-text is billed on student speech only (the examiner's voice is never transcribed).
- LLM tokens: the real scoring prompt (rubric + reference points + transcript) built from each sample, 3.8 characters per token, 900 output tokens per run (an estimate; the cap is 1,800).
- Not included: faculty time, the college's network, and a database server (SQLite on the same machine is enough for 350 students; Postgres is supported for multi-server setups).
