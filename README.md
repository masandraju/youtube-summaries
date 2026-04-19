# YouTube ↔ GitHub AI Orchestrator

An agentic AI system that fetches YouTube video transcripts, generates technical summaries using Claude, and pushes them to GitHub — all through a natural language interface.

---

## Architecture

```
User Input (YouTube URL / natural language commands)
         │
         ▼
┌─────────────────────────────────────────────────────┐
│                   ORCHESTRATOR                       │
│                                                     │
│  • Receives user input                              │
│  • Routes tasks to agents using Claude (LLM)        │
│  • Manages shared memory (SQLite)                   │
│  • Coordinates agent responses                      │
└──────────────┬──────────────────────┬───────────────┘
               │                      │
   ┌───────────▼──────────┐  ┌────────▼────────────┐
   │    YOUTUBE AGENT     │  │    GITHUB AGENT      │
   │                      │  │                      │
   │ • Validates URL      │  │ • Creates repo       │
   │ • Fetches transcript │  │ • Pushes files       │
   │ • Summarizes w/ LLM  │  │ • Fetches files      │
   │ • Caches results     │  │ • Lists repo content │
   └──────────────────────┘  └─────────────────────┘
               │                      │
   ┌───────────▼──────────────────────▼───────────┐
   │             MEMORY (SQLite)                   │
   │                                               │
   │  • Task history      • Summary cache          │
   │  • Session log       • GitHub URLs            │
   └───────────────────────────────────────────────┘
```

---

## Features

- **Natural language routing** — Claude decides which agent to call based on your input
- **YouTube transcript fetching** — no YouTube API key required
- **Technical summarization** — key points, concepts, tools, difficulty level
- **GitHub integration** — push summaries as markdown files, fetch them back
- **Persistent memory** — SQLite stores task history and caches summaries to avoid re-processing
- **Smart caching** — same video URL never processed twice

---

## Project Structure

```
youtube_github_orchestrator/
├── main.py                         # Entry point — run this
│
├── orchestrator/
│   ├── orchestrator.py             # Master controller + LLM-based routing
│   └── memory.py                   # SQLite persistent memory system
│
├── agents/
│   ├── base_agent.py               # Abstract base class all agents inherit
│   ├── youtube_agent.py            # Transcript fetch + Claude summarization
│   └── github_agent.py             # Push/fetch/list via GitHub API
│
├── models/
│   └── schemas.py                  # Pydantic data models (type-safe contracts)
│
├── utils/
│   └── logger.py                   # Rich terminal logging with colors
│
├── requirements.txt
├── .env                            # Your secrets (never committed)
└── .gitignore
```

---

## Tech Stack

| Component | Technology |
|---|---|
| LLM | Anthropic Claude Sonnet 4.6 / Haiku 4.5 |
| YouTube | `youtube-transcript-api` v0.6.x |
| GitHub | `PyGithub` |
| Memory | SQLite (built-in `sqlite3`) |
| Data Models | `pydantic` v2 |
| CLI | `rich` |
| Retry Logic | `tenacity` |
| Config | `python-dotenv` |

---

## Setup

### Prerequisites
- Python 3.10+
- Anthropic API key → [console.anthropic.com](https://console.anthropic.com)
- GitHub Personal Access Token (Classic) with `repo` scope → GitHub → Settings → Developer Settings → PAT

### Installation

```bash
# Clone the repo
git clone https://github.com/masandraju/youtube-summaries.git
cd youtube-summaries

# Install dependencies
pip install -r requirements.txt

# Configure your secrets
# Create a .env file and fill in your keys (see Environment Variables below)
```

### Configure `.env`

```env
ANTHROPIC_API_KEY=sk-ant-...
GITHUB_TOKEN=ghp_...
GITHUB_USERNAME=your_github_username
GITHUB_REPO_NAME=youtube-summaries
```

### Run

```bash
py main.py
```

---

## Usage

### Summarize a YouTube video
```
You → https://www.youtube.com/watch?v=dQw4w9WgXcQ
```

### Push summary to GitHub
```
You → push summary_dQw4w9WgXcQ.md
```

### Fetch a file from GitHub
```
You → fetch summary_dQw4w9WgXcQ.md
```

### List all files in GitHub repo
```
You → list
```

### View task history
```
You → history
```

---

## How It Works

### 1. LLM-Based Routing
Instead of rigid if/else command matching, user input is sent to **Claude Haiku** which returns a structured JSON routing decision — which agent to call, what action to perform, and what parameters to extract from the natural language input.

### 2. YouTube Agent Flow
```
URL → extract video ID → check memory cache
    → fetch transcript (youtube-transcript-api)
    → send to Claude Sonnet with structured prompt
    → parse JSON response into TechnicalSummary model
    → cache in SQLite
    → return to orchestrator
```

### 3. GitHub Agent Flow
```
Push: authenticate → get/create repo → create or update file → return URL
Fetch: authenticate → get repo → decode base64 content → return text
List: authenticate → get repo contents → return file list
```

### 4. Memory System
Every task is logged to SQLite with full input/output. Summaries are cached by `video_id` so the same video is never re-processed. The database persists across sessions.

---

## Summary Output Format

Each generated summary includes:

| Field | Description |
|---|---|
| Title | Inferred from transcript content |
| Overview | 2-3 sentence technical summary |
| Key Points | 5-8 main takeaways |
| Technical Concepts | Algorithms, patterns, paradigms mentioned |
| Tools & Libraries | Specific frameworks and tools discussed |
| Code Examples | Code snippets described or shown |
| Target Audience | Who the video is aimed at |
| Difficulty Level | Beginner / Intermediate / Advanced |

---

## Design Patterns Used

| Pattern | Where Used |
|---|---|
| **Orchestrator** | `orchestrator.py` — central controller coordinating agents |
| **Template Method** | `base_agent.py` — defines `run()` structure, subclasses implement `_execute()` |
| **Strategy** | Each agent is an interchangeable strategy for handling a task type |
| **Repository** | `memory.py` — abstracts all data persistence behind a clean interface |
| **DTO (Data Transfer Object)** | Pydantic schemas — typed contracts between components |

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `GITHUB_TOKEN` | Yes | GitHub PAT with `repo` scope |
| `GITHUB_USERNAME` | Yes | Your GitHub username |
| `GITHUB_REPO_NAME` | No | Defaults to `youtube-summaries` |

---

## Security Notes

- `.env` is in `.gitignore` and never committed
- GitHub token uses minimum required scope (`repo` only)
- All secrets loaded via `python-dotenv` — never hardcoded
- Input validated via Pydantic before reaching any agent

---

## License

MIT
