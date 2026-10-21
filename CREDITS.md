# Credits

Vaani is built on open-source software and openly licensed models and data. Thank you to their authors.

## Models and data

| Component | Used for | License |
|---|---|---|
| OpenAI Whisper (tiny/base/small, English and multilingual) | speech recognition | MIT |
| sherpa-onnx Whisper ONNX exports (k2-fsa) | the model files downloaded by `scripts/download_models.py` | Apache-2.0 |
| Silero VAD | detecting when the student starts and stops speaking | MIT |
| flite (through ffmpeg) | offline examiner voice and the voices of the test bots | BSD-style (CMU) |
| NPTEL2020 Indian English speech dataset (AI4Bharat) | speech-recognition accuracy evaluation; downloaded on demand, not redistributed | Creative Commons (NPTEL lecture content) |

## Server (Python)

| Component | Used for | License |
|---|---|---|
| Starlette, Uvicorn, wsproto | web framework, server, WebSockets | BSD-3-Clause / MIT |
| ONNX Runtime | running Whisper and Silero VAD | MIT |
| NumPy | audio features, mixing, analysis | BSD-3-Clause |
| Pydantic | validation | MIT |
| HTTPX | hosted speech and AI providers | BSD-3-Clause |
| PyJWT, cryptography | sessions; recordings encrypted at rest | MIT / Apache-2.0 or BSD |
| psutil | system page and load-test monitor | BSD-3-Clause |
| FFmpeg (external program) | Opus encoding, audio decoding | LGPL-2.1+ |

## Web app

| Component | Used for | License |
|---|---|---|
| React, React DOM | user interface | MIT |
| esbuild, TypeScript | build | MIT / Apache-2.0 |
| Mukta, Kalam, JetBrains Mono (self-hosted) | typefaces | SIL Open Font License 1.1 |

Optional integrations (used only when configured): Groq, OpenAI and Sarvam AI APIs; Ollama; faster-whisper (MIT);
Piper TTS (MIT).
