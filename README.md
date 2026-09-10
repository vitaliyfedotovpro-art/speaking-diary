# Speaking Diary

A personal diary that listens, remembers and talks back. Runs entirely on your own
machine: your entries never leave it except as requests to the model you choose.

## What it is

Two ways in, one memory behind both:

**The notebook** — pages you leaf through, conversations grouped by day, each with a
title and what the diary took away from it. Type, or tap the microphone and speak.
Works on a free API tier.

**Live voice** — you talk, it answers out loud, you can interrupt it. Needs a paid
Gemini plan; the notebook does not.

## Memory

Facts are kept by [Astrum HSAM](https://github.com/vitaliyfedotovpro-art/astrum-hsam-embedded),
a memory engine that does two things a vector store does not:

- **A model's own output cannot become its evidence.** Self-descriptions are
  quarantined from recall outright, not merely down-ranked.
- **What matters survives.** Facts marked as canon are never evicted under memory
  pressure — exactly the rarely-read entries a least-recently-used policy drops first.

Conversations are stored separately from facts, on purpose: raw dialogue in memory
drowns the facts, and facts without the dialogue lose how something was said.

## Keys

| key | what for | tier |
|---|---|---|
| Gemini | conversation, memory, the diary's voice | free tier works; live voice and privacy need a paid plan |
| Groq | turns speech into text (Whisper) | free, no card |

On Gemini's free tier Google uses your conversations to improve its models. Adding a
card in Google Cloud stops that. It is your diary — your call.

## Build

macOS / Linux:

```
pip install -r requirements.txt pyinstaller
pyinstaller --clean --noconfirm diary.spec
```

Windows builds run in CI (`.github/workflows/build.yml`) — PyInstaller does not
cross-compile, and neither does the Rust engine.

## Running from source

```
pip install -r requirements.txt
python -m diary.server
```

Data lives in `~/.diary`: memory snapshot, conversation journal, attachments, keys.
Back it up from the settings panel — to an external drive or a cloud folder.
