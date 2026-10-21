# Question banks and the rubric

The examiner only asks what the faculty's bank contains (or a logged rewording of it). This page explains the bank
format, how to build one, and how the rubric works. Two complete banks are included:
`samples/question_banks/dbms.json` (29 questions) and `os.json` (20), each also as `.csv`.

## Three ways to build a bank

1. **Upload a file** (Question banks, then Import): JSON or CSV in the format below. Imported questions arrive as
   drafts unless "approve all" is ticked.
2. **Generate from a syllabus** (Question banks, then Generate): paste the syllabus or unit outline; Vaani drafts
   questions with key points, follow-ups and hints. With no AI provider configured it uses an offline template, so
   the screen always works. Generated questions are always drafts: a faculty member must read and approve each one
   before a viva can use it.
3. **Write them in the editor**: every field below has a form control, with a live check for hints that give away
   the answer.

A bank is **ready** for a viva when it has at least 3 approved fundamentals questions, 1 depth question and 1 applied
problem. The bank page also shows warnings that do not block (no hint, no rephrasing, a hint that leaks key terms,
too few questions for variety) and the coverage per topic and phase. Editing an approved bank creates a new version;
each viva attempt stores the exact questions it used, so a later edit never changes an old transcript's meaning.

## One question

```json
{
  "code": "DBMS-F-02",
  "topic": "Keys and integrity",
  "phase": "fundamentals",
  "type": "concept",
  "difficulty": 2,
  "question": "Explain super key, candidate key and primary key, and how they are related.",
  "rephrase": "What are super keys and candidate keys, and where does the primary key fit in?",
  "key_points": [
    {"point": "Candidate key: a minimal super key", "terms": ["minimal", "no extra attribute", "irreducible"]}
  ],
  "follow_ups": [
    {"text": "In a Students table with roll number, email and name, which could be candidate keys and why?",
     "kind": "example",
     "key_points": [{"point": "Name is not unique", "terms": ["name is not unique", "names can repeat"]}]}
  ],
  "hints": ["Start from the job a key does in a table, then ask whether every column in a key set is really needed."],
  "misconceptions": [{"text": "Candidate key and super key are the same thing", "terms": ["same thing"]}],
  "expected_seconds": 60
}
```

| Field | Meaning |
|---|---|
| `code` | Stable id, shown in transcripts as `bank:DBMS-F-02`. Generated if empty. |
| `topic` | Used to spread questions across the syllabus and to pick the student's strongest topic for the depth phase. |
| `phase` | `warmup`, `fundamentals`, `depth` or `applied` (the 15-minute plan below). |
| `type` | `concept`, `scenario`, `debug` (with `code_snippet`) or `project` (warm-up about the student's own work). |
| `difficulty` | 1 to 3. Struggling students get easier questions next; strong students get harder ones. |
| `question`, `rephrase` | What the examiner says, and a second wording for "can you repeat / explain the question". |
| `key_points` | What a complete answer covers. `terms` are words or stems that show the point was made (aliases and Hinglish are handled). They decide strong / partial / weak and feed the scorer as reference points. They are **never** read out. |
| `follow_ups` | Asked after a strong answer to go deeper. Each can carry its own key points and `when` terms (ask this one first if the answer mentioned them). |
| `hints` | Given once after a partial answer. Must not contain the answer: the bank check flags hints that mention key terms. |
| `misconceptions` | Common wrong ideas; if one is heard, the examiner probes it instead of moving on, and the scorer sees it. |
| `expected_seconds` | Typical answer length (the hard cap is 90 seconds). |

The bank file also has `name`, `subject`, `description` and `aliases` (spoken or abbreviated forms mapped to one
spelling, for example `"2pl": "two phase locking"`, `"b plus tree": "b+ tree"`), which help both matching and the
speech recogniser.

## CSV

One question per row with the columns
`code, topic, phase, type, difficulty, question, rephrase, key_points, follow_ups, hints, misconceptions, code_snippet, expected_seconds, tags`.

Cells can be written by hand in a short form:

| Column | Short form |
|---|---|
| `key_points` | `Atomicity: atomic / all or nothing; Durability: durable / survives crash` |
| `follow_ups` | `why|Why is this needed? || what_if|What if two users write at once?` |
| `hints`, `tags` | `first hint || second hint` |
| `difficulty` | `1`, `2`, `3` or `easy`, `medium`, `hard` |

Any cell may instead hold JSON (the exporter uses JSON whenever the short form would lose something, such as
follow-ups that have their own key points), so export and re-import is exact; a test checks this for both banks.

## The 15-minute plan

| Time | Phase | What happens |
|---|---|---|
| 0:00 to 1:00 | Warm-up | Identity check, then one question about the student's own project |
| 1:00 to 5:00 | Fundamentals | At least 3 questions from different topics |
| 5:00 to 10:00 | Depth | Follow-ups on the strongest topic, "why" and "what if" |
| 10:00 to 13:00 | Applied problem | A scenario or debugging task, reasoning aloud |
| 13:00 to 14:30 | Wrap-up | "Anything you want to add?" and a neutral close |
| 15:00 | Hard stop | The viva ends on time whatever is happening |

A phase may start early only once the previous phase has asked its minimum number of questions. Faculty can change
the phase boundaries per viva (with a written justification, kept with the viva).

## The rubric

`samples/rubric.json` (also the default for new vivas):

| Dimension | Weight | 5 means |
|---|---|---|
| Conceptual understanding | 30 | Accurate, precise explanations; correct terms used naturally |
| Depth and reasoning | 25 | Explains why, handles "what if", connects ideas |
| Problem solving | 20 | Structured approach to the applied problem, considers alternatives |
| Practical / project knowledge | 15 | Real examples, trade-offs, links to own work |
| Communication | 10 | Clear and organised; accent, grammar and language choice are not penalised |

Each dimension gets 0 to 5 with a short reason that cites transcript lines (with their timestamps), the total is
scaled to 100, and the feedback has 3 to 5 lines. Faculty can change the weights and the anchor descriptions per viva;
the rubric version and a hash of it are stored with every score.
