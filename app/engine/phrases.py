"""Everything the examiner says that is not a bank question.

All lines are neutral: the examiner never says "correct", "wrong", "exactly" or hints at an answer, so students get
no feedback mid-viva that could favour one student over another. Lines with a stable id are synthesised once per
voice and cached, so they play instantly.
"""
from __future__ import annotations

ACKS = ["Okay.", "Alright.", "Thank you.", "I see.", "Got it.", "Okay, thanks.", "Noted.", "Right."]
IDK_ACKS = ["No problem.", "That's fine.", "Okay, no worries.", "That's alright."]

LINES = {
    "rules": ("Thank you. A quick note before we start: this viva has five short parts and lasts at most fifteen minutes. "
              "Answer out loud, in your own words. It's completely fine to say you don't know, or to ask me to repeat a question."),
    "rules_unconfirmed": ("Thank you. A quick note before we start: this viva has five short parts and lasts at most fifteen minutes. "
                          "Answer out loud, in your own words. It's completely fine to say you don't know, or to ask me to repeat a question."),
    "identity_again": "Sorry, I didn't catch that. Please say your name and roll number.",
    "to_fundamentals": "Let's move on to some fundamentals.",
    "to_applied": "Now let's try a practical problem. Please think out loud as you work through it.",
    "to_applied_code": "Now let's try a practical problem. I've put some code on your screen. Please think out loud as you work through it.",
    "to_wrapup": "We're nearly done.",
    "wrapup_q": "Is there anything you'd like to add or clarify about any of your answers?",
    "redirect": "Let's come back to the question.",
    "redirect_injection": "I can only ask questions and listen to your answers in this viva. Let's come back to the question.",
    "think": "Sure, take your time.",
    "silence_1": "Take your time. Would you like me to repeat the question?",
    "silence_rephrase": "Let me put that another way.",
    "silence_move_on": "That's okay. Let's move on.",
    "didnt_catch": "Sorry, I didn't catch that. Could you say it again?",
    "cut": "Thank you. Let me stop you there so that we can cover more ground.",
    "move_on": "Let's move on.",
    "different_area": "Let's try a different area.",
    "next_question": "Here's the next one.",
    "repeat_intro": "Sure.",
    "hint_intro": "Here's a hint.",
    "probe_why": "Why does it work that way? Explain the reasoning behind what you just said.",
    "probe_what_if": "What would change if the system were ten times larger?",
    "probe_example": "Can you give me a concrete example of that?",
    "probe_project": "What was the hardest problem you faced in that project, and how did you solve it?",
    "close": ("Thank you. That's the end of your viva. Your answers will be reviewed and your faculty will share the results. "
              "You can close this window now."),
    "close_time": ("We're out of time. Thank you for your answers. That's the end of your viva, and your faculty will share "
                   "the results. You can close this window now."),
    "close_early": "Okay. We'll end the viva here. Thank you. Your faculty will share the results.",
    "mic_check": "I can't hear you clearly. Please check that your microphone is on and unmuted.",
}


def greeting(first_name: str, subject: str) -> str:
    name = f" {first_name}" if first_name else ""
    return (f"Hello{name}. I'm Vaani, your examiner for {subject} today. "
            f"Before we begin, please say your full name and your roll number.")


def depth_intro(topic: str) -> str:
    t = topic if topic.isupper() or any(w.isupper() and len(w) > 1 for w in topic.split()) else topic.lower()
    return f"Let's go a little deeper into {t}."


def ack(i: int) -> str:
    return ACKS[i % len(ACKS)]


def idk_ack(i: int) -> str:
    return IDK_ACKS[i % len(IDK_ACKS)]


def all_cacheable() -> list[str]:
    """Every fixed line, for pre-synthesis when a viva is opened."""
    return [*ACKS, *IDK_ACKS, *LINES.values()]
