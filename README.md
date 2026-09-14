# Speaking Diary

A personal diary that listens, remembers and talks back. Runs entirely on your own
machine: your entries never leave it except as requests to the model you choose.

## Install

**Windows.** Download `Diary.exe` and double-click it. That's the whole install —
nothing to set up, no dependencies. The diary opens in your browser; it runs only on
your own machine and never listens to the network.

The first time you run it, Windows shows a blue **"Windows protected your PC"** screen.
This is because the file isn't signed by a big company, not because anything is wrong.
Click **More info**, then **Run anyway**. It only ever asks once.

Windows may also ask whether the app can use your network — allow it. The diary talks
to your machine only (`127.0.0.1`); the prompt is a standard one for anything that opens
a local page.

**macOS.** Download `Diary-macos-arm64` (Apple Silicon). It isn't notarised, so
Gatekeeper refuses it on a double-click — open a terminal where you downloaded it:

```
chmod +x Diary-macos-arm64
xattr -d com.apple.quarantine Diary-macos-arm64
./Diary-macos-arm64
```

**Linux.** Download `Diary-linux-x86_64`, `chmod +x` it and run it. Built on
Ubuntu 24.04, so it needs glibc 2.39 or newer — Ubuntu 24.04+, Debian 13+,
Fedora 40+. On older distributions build from source instead (below).

## What it is

Two ways in, one memory behind both:

**The notebook** — pages you leaf through, conversations grouped by day, each with a
title and what the diary took away from it. Type, or tap the microphone and speak.

**Live voice** — you talk, it answers out loud, you can interrupt it.

Both run on a free Gemini key. Live voice has its own quota, separate from the text
one and generous; the notebook's spoken replies are the part that runs out first, at
ten a day. See [Keys](#keys) for the measured numbers.

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
| Gemini | conversation, memory, the diary's voice | free tier works, limits below |
| Groq | turns speech into text (Whisper) | free, no card |

On Gemini's free tier Google uses your conversations to improve its models. Adding a
card in Google Cloud stops that. It is your diary — your call.

### What the free tier actually gives you

Measured on a live free key, 13–14 September 2026 — not read off a pricing page:

| | free tier |
|---|---|
| live voice (Arc Reactor) | its own quota; no ceiling found in testing |
| memory search | no limit hit |
| text model | 5 requests a minute, 20 a day — **per model** |
| spoken replies in the notebook | 10 a day |

The daily counters are per project and reset at midnight Pacific. The diary spreads its
background work over three different models on purpose, so conversation, fact extraction
and titles each get their own allowance instead of sharing one.

Enabling billing removes the free tier for that project entirely. Paid, an ordinary day
of use — half an hour of conversation, fifty-odd turns — costs about **$0.20**.

## Build

macOS / Linux:

```
pip install -r requirements.txt pyinstaller
pyinstaller --clean --noconfirm diary.spec
```

**Windows** builds run in GitHub Actions, because neither PyInstaller nor the Rust
engine cross-compiles from macOS. Push a tag and the `.exe` appears as an artifact:

```
git tag v0.1.0 && git push origin main --tags
```

The workflow (`.github/workflows/build.yml`) checks out the HSAM engine, compiles it
to `astrum_memory.dll`, and packs everything into a single `Diary.exe`. Download it from
the run's Artifacts on the Actions tab.

## Running from source

```
pip install -r requirements.txt
python -m diary.server
```

Data lives in `~/.diary`: memory snapshot, conversation journal, calendar, attachments,
keys. Back it up from the settings panel — to an external drive or a cloud folder.

Environment variables, all optional:

| variable | default | what it does |
|---|---|---|
| `DIARY_HOME` | `~/.diary` | where the diary keeps everything |
| `DIARY_PORT` | `8791` | page; the voice bridge takes the next port up |
| `DIARY_CHAT_MODEL` | `gemini-3.8-flash` | the conversation itself |
| `DIARY_EXTRACT_MODEL` | `gemini-2.5-flash` | turning conversations into facts |
| `DIARY_TITLE_MODEL` | `gemini-3.5-flash` | titles and the link questions |
| `DIARY_LIVE_MODEL` | `gemini-2.5-flash-native-audio-latest` | live voice |
| `DIARY_LIVE_VOICE` | `Kore` | which prebuilt voice speaks |
| `DIARY_BACKUP_DIR` | — | where hourly copies go |

The three text models are deliberately different: free-tier quota is counted per model,
so splitting the work triples what a free key can do in a day.

## License

Apache License 2.0 — see [LICENSE](LICENSE).

The memory engine it links, [Astrum HSAM](https://github.com/vitaliyfedotovpro-art/astrum-hsam-embedded),
is Apache-2.0 as well.
