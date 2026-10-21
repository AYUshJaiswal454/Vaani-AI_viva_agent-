"""Speech: the built-in Whisper recogniser, WER maths, the voice cache, hosted adapters and the follow-up leak guard."""
from __future__ import annotations

import asyncio
import unittest
from pathlib import Path

import numpy as np

from tests import llm_stub
from tests.support import ROOT, fresh_environment

from loadtest.bot import synth

MODELS = ROOT / "models"


def have_whisper(name: str) -> bool:
    from app.voice.stt.whisper_onnx import find_model_dir

    return find_model_dir(MODELS, name) is not None


class Features(unittest.TestCase):
    def test_mel_filterbank_shape_and_order(self):
        from app.voice.stt.whisper_onnx import mel_filters

        m = mel_filters(80)
        self.assertEqual(m.shape, (80, 201))
        self.assertTrue((m >= 0).all())
        peaks = m.argmax(axis=1)
        self.assertTrue(all(peaks[i] <= peaks[i + 1] for i in range(79)))

    def test_log_mel_range(self):
        from app.voice.stt.whisper_onnx import log_mel

        x = np.sin(np.linspace(0, 440 * 2 * np.pi, 16000)).astype(np.float32) * 0.3
        mel = log_mel(x)
        self.assertEqual(mel.shape[0], 80)
        self.assertEqual(mel.shape[1], 100)                  # 10 ms hop
        self.assertLessEqual(mel.max(), 1.6)

    def test_bpe_round_trip(self):
        if not have_whisper("tiny.en") and not have_whisper("base.en"):
            self.skipTest("no whisper model downloaded")
        from app.voice.stt.whisper_onnx import find_model_dir, Tokenizer

        name = "base.en" if have_whisper("base.en") else "tiny.en"
        tok = Tokenizer(find_model_dir(MODELS, name) / f"{name}-tokens.txt")
        text = " normalisation, B+ tree, serializable isolation"
        self.assertEqual(tok.decode(tok.encode(text)), text)


@unittest.skipUnless(have_whisper("tiny.en") or have_whisper("base.en"), "no whisper model downloaded")
class WhisperOnnx(unittest.TestCase):
    def test_recognises_speech(self):
        from app.voice.stt.whisper_onnx import get_model
        from app.voice.wer import wer

        name = "base.en" if have_whisper("base.en") else "tiny.en"
        m = get_model(MODELS, name)
        ref = "A primary key uniquely identifies each row and it can never be null."
        tr = m.transcribe(synth(ref, "slt"))
        self.assertLess(wer(ref, tr.text)["wer"], 0.35, tr.text)
        self.assertGreater(tr.confidence, 0.4)

    def test_silence_has_low_confidence(self):
        from app.voice.stt.whisper_onnx import get_model

        name = "base.en" if have_whisper("base.en") else "tiny.en"
        tr = get_model(MODELS, name).transcribe(np.zeros(16000 * 2, np.float32))
        self.assertTrue(tr.text.strip() == "" or tr.no_speech_prob > 0.3 or tr.confidence < 0.6, tr)

    def test_long_answers_are_split(self):
        from app.voice.stt.whisper_onnx import split_long

        x = np.random.default_rng(0).standard_normal(16000 * 70).astype(np.float32) * 0.01
        parts = split_long(x)
        self.assertGreaterEqual(len(parts), 3)
        self.assertEqual(sum(len(p) for p in parts), len(x))
        self.assertTrue(all(len(p) <= 16000 * 28 for p in parts))


class WER(unittest.TestCase):
    def test_wer(self):
        from app.voice.wer import corpus_wer, term_recall, wer

        self.assertEqual(wer("a b c d", "a b c d")["wer"], 0)
        w = wer("the cat sat on the mat", "the cat sit on mat")
        self.assertEqual((w["sub"], w["del"], w["ins"]), (1, 1, 0))
        self.assertAlmostEqual(w["wer"], 2 / 6)
        self.assertEqual(wer("Normalisation, um, matters", "normalization matters")["wer"], 0)
        self.assertAlmostEqual(corpus_wer([("a b", "a c"), ("x y z w", "x y z w")])["wer"], 1 / 6)
        self.assertEqual(term_recall("we use a B plus tree index", "we use a b plus tree", ["B plus tree", "index"]), (1, 2))


class VoiceCache(unittest.TestCase):
    def test_clip_is_synthesised_once(self):
        fresh_environment(tts_engine="flite")
        from app.db import get_db
        from app.voice import tts

        a = asyncio.run(tts.get_clip("Take your time.", "flite"))
        n = get_db().scalar("SELECT COUNT(*) FROM usage_ledger WHERE feature = 'tts'")
        b = asyncio.run(tts.get_clip("Take your time.", "flite"))
        self.assertEqual(a["key"], b["key"])
        self.assertEqual(get_db().scalar("SELECT COUNT(*) FROM usage_ledger WHERE feature = 'tts'"), n)
        self.assertGreater(a["duration_s"], 0.3)


class HostedAdapters(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.base = llm_stub.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True

    def test_openai_compatible_stt_and_tts(self):
        fresh_environment(openai_api_key="test", openai_base_url=self.base, groq_api_key="test", groq_base_url=self.base)
        from app.voice import stt, tts

        pcm = (synth("atomicity", "kal16") * 32767).astype("<i2").tobytes()
        r = asyncio.run(stt.transcribe(pcm, engine="openai", prompt="atomicity, durability"))
        self.assertEqual(r.text, "atomicity means all or nothing")
        g = asyncio.run(stt.transcribe(pcm, engine="groq"))
        self.assertIsNotNone(g.confidence)
        self.assertTrue(any(c.get("kind") == "stt" and "atomicity" in (c.get("prompt") or "") for c in llm_stub.CALLS))
        clip = asyncio.run(tts.get_clip("Hello student.", "openai"))
        self.assertTrue(clip and clip["duration_s"] > 0)

    def test_hosted_recognition_waits_out_a_rate_limit(self):
        fresh_environment(groq_api_key="test", groq_base_url=self.base, whisper_model="not-downloaded")
        from app.voice import stt

        llm_stub.STT_RATE_LIMIT_ONCE = True
        try:
            pcm = (synth("atomicity", "kal16") * 32767).astype("<i2").tobytes()
            r = asyncio.run(stt.transcribe(pcm, engine="groq"))
        finally:
            llm_stub.STT_RATE_LIMIT_ONCE = False
        self.assertEqual(r.error, "")
        self.assertEqual(r.text, "atomicity means all or nothing")

    def test_failed_recognition_never_raises(self):
        fresh_environment(openai_api_key="test", openai_base_url="http://127.0.0.1:9/v1", whisper_model="not-downloaded")
        from app.voice import stt

        r = asyncio.run(stt.transcribe(b"\x00\x00" * 16000, engine="openai"))
        self.assertEqual(r.text, "")
        self.assertTrue(r.error)

    def test_hosted_failure_falls_back_to_local_whisper(self):
        from app.voice.stt.whisper_onnx import find_model_dir

        if not find_model_dir(ROOT / "models", "base.en"):
            self.skipTest("Whisper base.en not downloaded")
        fresh_environment(openai_api_key="test", openai_base_url="http://127.0.0.1:9/v1", whisper_model="base.en")
        from app.voice import stt

        speech = synth("A primary key uniquely identifies each row.", "kal16")
        r = asyncio.run(stt.transcribe((np.clip(speech, -1, 1) * 32767).astype("<i2").tobytes(), engine="openai"))
        self.assertFalse(r.error)
        self.assertEqual(r.engine, "whisper-onnx")
        self.assertTrue(r.fallback_from.startswith("openai"))
        self.assertIn("primary key", r.text.lower())


class LeakGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.base = llm_stub.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True

    REQ = {"base": "Why is atomicity hard to guarantee if the server crashes in the middle of a transaction?",
           "source": "bank:DBMS-F-03/f1", "question": "What do the ACID properties of a transaction mean?",
           "answer": "Consistency keeps the database valid, isolation stops interference.",
           "key_points": ["Partial writes may already be on disk", "Needs a log to undo/redo (rollback)"]}

    def run_variant(self, model: str):
        from app.engine.variants import phrase_followup

        fresh_environment(llm_provider="openai", openai_api_key="test", openai_base_url=self.base, openai_model_live=model,
                          live_llm_timeout_s=1.0)
        return asyncio.run(phrase_followup(dict(self.REQ), 1))

    def test_good_variant_is_used(self):
        text, info = self.run_variant("stub-scorer")
        self.assertTrue(info["used"], info)
        self.assertTrue(text.endswith("?"))

    def test_leaky_variant_is_rejected(self):
        text, info = self.run_variant("stub-leaky")
        self.assertIsNone(text)
        self.assertIn("leak guard", info["why"])

    def test_slow_model_falls_back_to_bank_wording(self):
        text, info = self.run_variant("stub-slow")
        self.assertIsNone(text)
        self.assertFalse(info["used"])


if __name__ == "__main__":
    unittest.main()
