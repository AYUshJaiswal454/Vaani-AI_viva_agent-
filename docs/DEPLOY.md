# Deploy your own Vaani

A step-by-step guide for anyone who wants to fork this repository and run their own copy of Vaani, the AI viva
examiner, with every feature working. No coding is needed.

Repository: **github.com/AYUshJaiswal454/Vaani-AI_viva_agent-**

## 1. Choose how to run it

Every setup below runs **the same Vaani with all features**: voice viva with live captions, adaptive follow-ups,
hints, the 15-minute timer, rubric scoring with evidence, recordings, faculty review, overrides, the audit trail and
exports. What changes is **where the student's speech is turned into text**, how many students can take a viva at
the same time, and whether data is kept after a restart.

| | **A. Render Free + Groq key** | **B. Render paid** | **C. Your own computer or college server** |
|---|---|---|---|
| Cost | Free | $25/month (1 CPU, 2 GB) + $0.25/GB/month for a disk | Free (your hardware) |
| Speech recognition | Groq Whisper large-v3-turbo (any browser) | Groq, or Vaani's built-in Whisper | Vaani's built-in Whisper, on your machine |
| AI scorer and AI-worded follow-ups | Yes (Groq) | Yes (Groq) | Yes, with a local model (Ollama) or a Groq key |
| Students at the same time | a few (Groq's free plan allows about 20 recognitions a minute) | about 3 with built-in Whisper; with a paid Groq plan, as many as the server handles (150 on 2 CPUs in our load test) | about 3 per CPU core with built-in Whisper |
| Always on | No: sleeps after 15 idle minutes, wakes in about a minute | Yes | Yes |
| Data kept after restart | No (demo data reloads automatically) | Yes (with a disk) | Yes |
| Best for | trying it, demos, judges | a pilot in one department | real exams, nothing leaves the campus |

> **Important for setup A:** add the free Groq key. Without it Vaani still works, but speech recognition falls back
> to the student's browser (Chrome or Edge only) and marking uses only the rule-based scorer. With the key nothing is
> lost.

## 2. What you need

- A **GitHub** account (free): github.com
- For A or B: a **Render** account (free): render.com, sign up with GitHub
- For A or B (and optional for C): a **Groq** account (free): console.groq.com
- For C: a computer with **Docker** (Docker Desktop on Windows or Mac, Docker Engine on Linux), 4 CPU cores and
  8 GB of memory recommended

## 3. Setup A: Render Free with a Groq key (about 15 minutes)

**Step 1. Fork the repository.**
Open the repository on GitHub, sign in, click **Fork** (top right), then **Create fork**. You now have your own copy at
`github.com/YOUR-NAME/Vaani-AI_viva_agent-`.

**Step 2. Get a free Groq API key.**
Go to console.groq.com and sign in (Google works). Open **API Keys** and click **Create API Key**, give it a name such
as `vaani`, and copy the key (it starts with `gsk_`). Keep it private: never paste it into GitHub, a README or a chat.

**Step 3. Create the service on Render.**
In the Render dashboard click **New > Blueprint**. Click **Connect** next to your fork. If your fork is not in the
list, use **Configure account** to give Render access to it. Render reads the `render.yaml` file in the repository and
shows one web service called `vaani` (Docker, free plan).

**Step 4. Fill in the values Render asks for.**

| Name | What to enter |
|---|---|
| `GROQ_API_KEY` | the key from step 2 |
| `ADMIN_PASSWORD` | a password for the administrator account `admin@vaani.local` |
| `DEMO_FACULTY_PASSWORD` | a password for the demo faculty account `faculty@vaani.local` (blank means `faculty123`) |

Then click **Deploy Blueprint**.

**Step 5. Wait for the first build.**
Open the `vaani` service and watch **Logs**. The first build takes about 5 to 10 minutes. When the status shows
**Live**, your address appears at the top, for example `https://vaani-abcd.onrender.com`.

**Step 6. Check that every feature is on.**
Open `https://YOUR-ADDRESS/api/health`. You should see `"ok": true` and `"stt": "groq"`. Then sign in as faculty
(`/login`) and open **System**: speech recognition should say **groq** and the language model **groq**. If it says
`browser` or `none`, the Groq key is missing or mistyped (see Troubleshooting).

**Step 7. Take a test viva.**
Faculty: open **DBMS mid-semester viva**; five recorded sample vivas are already there. Student: open the home page
in any modern browser, enter the code **DBMS26**, a roll number and a name, allow the microphone, and start.

> Free plan habits: open the site a minute before a demo (it sleeps after 15 idle minutes), and download anything you
> want to keep (the CSV export or "Download full record"), because data resets when the service restarts.

## 4. Setup B: Render paid, always on, data kept

Do Setup A first, then in the `vaani` service on Render:

1. **Settings > Instance type**: choose **1 CPU, 2 GB** ($25/month) or larger.
2. **Disks > Add disk**: mount path `/data`, size 1 GB (enough for about 280 recorded vivas; recordings older than
   180 days are deleted automatically).
3. Optional, to recognise speech without Groq: **Environment** > set `DOWNLOAD_MODELS` to `1`, then
   **Manual Deploy > Clear build cache & deploy**. The built-in Whisper is then used for speech (it takes priority
   when present); keep `GROQ_API_KEY` for the AI scorer.
4. Optional: set `RETENTION_DAYS` (default 180) and `CONSENT_CONTACT` (your college's data contact).

## 5. Setup C: your own computer or college server (Docker)

```bash
git clone https://github.com/YOUR-NAME/Vaani-AI_viva_agent-.git
cd Vaani-AI_viva_agent-
cp .env.example .env            # then edit ADMIN_PASSWORD (and GROQ_API_KEY if you have one)
docker compose up --build       # first build downloads the speech models (about 200 MB)
```

Open **http://localhost:8000**. Speech is recognised on this machine by the built-in Whisper; nothing is sent to any
outside service.

- **AI scorer without any key** (local model, needs about 8 GB of memory):
  `LLM_PROVIDER=ollama docker compose --profile local-ai up --build`
- **Without Docker**: install Python 3.11+ and ffmpeg, then run `./run.sh` (Linux, Mac) or
  `powershell -ExecutionPolicy Bypass -File run.ps1` (Windows).
- **Students on other computers**: browsers allow the microphone only on `https://` addresses (or on `localhost`).
  Put the server behind HTTPS, for example Caddy or nginx with a certificate, and give students that address.
- **More students at once**: about 3 concurrent vivas per CPU core with the built-in Whisper. With a hosted
  recogniser (a paid Groq plan, OpenAI or Sarvam) and `WEB_WORKERS=2`, one 2-core machine handled 150 concurrent
  vivas in our load test (recognition simulated with a 250 ms delay; see `docs/SCALABILITY.md`).

## 6. Keep your copy up to date

When the original repository gets improvements, open your fork on GitHub and click **Sync fork > Update branch**.
Render redeploys automatically. With Docker, run `git pull` and `docker compose up --build` again.

## 7. Troubleshooting

| What you see | What to do |
|---|---|
| Students are told to "open this page in Chrome or Edge" | The Groq key is not set: Render > `vaani` > **Environment** > add `GROQ_API_KEY` > **Save**, then redeploy |
| `/api/health` shows `"stt": "browser"` | Same as above; check the key has no spaces |
| The site takes about a minute to open | The free plan was asleep; it wakes on the first visit |
| Earlier vivas disappeared | The free plan has no disk; use Setup B or C to keep data |
| "Microphone blocked" | Click the lock icon in the address bar and allow the microphone; use the `https://` address |
| The AI score appears a few minutes after the viva, or the review says "AI scorer failed" | Groq's free plan limits requests per minute and per day; Vaani waits and retries, and marks with the rule-based scorer if the limit is reached. Use **Rescore** later, or a paid Groq plan |
| The build failed | Open **Logs**; try **Manual Deploy > Clear build cache & deploy** |
| Can't sign in | Use the passwords you set in step 4 (blank faculty password means `faculty123`) |

## 8. Safety checklist

- Never put API keys in GitHub. Vaani reads them only from Render's **Environment** page or your `.env` file, which
  is excluded from Git.
- Set your own `ADMIN_PASSWORD` and `DEMO_FACULTY_PASSWORD` on any public deployment.
- Recordings are encrypted at rest and deleted after `RETENTION_DAYS`. See `docs/PRIVACY.md` before using Vaani with
  real students, and test only with people who have agreed to be recorded.
