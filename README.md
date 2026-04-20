# YouTube ↔ GitHub ↔ JIRA AI Orchestrator

A production-grade multi-agent AI system that understands natural language and orchestrates three specialized agents — YouTube, GitHub, and JIRA — powered by Claude Sonnet 4.6.

Paste a YouTube URL to get a technical summary. Say "Create JIRA" to trigger a fully autonomous pipeline that creates a ticket, generates code, pushes it to GitHub, performs a code review, raises a PR, and updates the ticket — all without manual intervention.

---

## Architecture

```
User Input (natural language / YouTube URL / commands)
         │
         ▼
┌──────────────────────────────────────────────────────────────┐
│                        ORCHESTRATOR                          │
│                                                              │
│  • Receives user input (web UI or CLI)                       │
│  • Routes to the correct agent using Claude Haiku (LLM)      │
│  • Manages multi-step state machine (pending flows)          │
│  • Coordinates full autonomous pipelines                     │
│  • Persists all data in SQLite                               │
└────────────┬──────────────────┬──────────────────┬───────────┘
             │                  │                  │
 ┌───────────▼──────┐  ┌────────▼────────┐  ┌─────▼──────────┐
 │  YOUTUBE AGENT   │  │  GITHUB AGENT   │  │   JIRA AGENT   │
 │                  │  │                 │  │                │
 │ • Validates URL  │  │ • Create repo   │  │ • Create ticket│
 │ • Fetches transc │  │ • Push files    │  │ • Fetch ticket │
 │ • Claude summary │  │ • Create branch │  │ • Update status│
 │ • Caches results │  │ • Push code     │  │ • Add comments │
 │                  │  │ • Create PR     │  │ • Link PR URLs │
 │                  │  │ • Code review   │  │                │
 └──────────────────┘  └─────────────────┘  └────────────────┘
             │                  │                  │
 ┌───────────▼──────────────────▼──────────────────▼───────────┐
 │                    MEMORY (SQLite)                           │
 │                                                              │
 │  tasks · summaries · session_log · app_state · jira_tickets  │
 └──────────────────────────────────────────────────────────────┘
```

---

## What It Can Do

### YouTube Agent
- Paste any YouTube URL → get a structured technical summary
- Extracts: overview, key points, concepts, tools/libraries, difficulty level, target audience
- Caches results in SQLite — same video is never re-processed
- Push the summary as a Markdown file to GitHub with one click

### GitHub Agent
- Push / fetch / list Markdown files in your GitHub repo
- **Create feature branches** from main
- **Push generated code** files to a branch
- **Open Pull Requests** automatically
- **Code review** any code using Claude — returns score (1–10), issues, security concerns, suggestions

### JIRA Agent
- Fetch any ticket by key (e.g. `SCRUM-42`)
- Update ticket status through workflow transitions
- Add comments to tickets
- Link PR URLs back to tickets

### Full Autonomous Pipeline
Say **"Create JIRA"** and the orchestrator does everything automatically:

```
1. Ask what the ticket is about (ONE question if not provided)
2. Create JIRA ticket → get ticket key (e.g. SCRUM-5)
3. Generate Python code with Claude Sonnet
4. Create feature branch → feature/scrum-5-<slug>
5. Push generated code to the branch
6. Run code review → score out of 10
7. If score ≥ 6 → create Pull Request
8. Update JIRA → "In Review" + link PR URL
9. Return full results in one card
```

---

## Project Structure

```
youtube_github_orchestrator/
│
├── app.py                          # Flask web server (main entry point)
├── main.py                         # Optional CLI entry point
│
├── orchestrator/
│   ├── orchestrator.py             # Master controller, LLM routing, pipeline logic
│   └── memory.py                   # SQLite persistence layer
│
├── agents/
│   ├── base_agent.py               # Abstract base — Template Method pattern
│   ├── youtube_agent.py            # Transcript fetch + Claude summarization
│   ├── github_agent.py             # GitHub API — push/fetch/branch/PR/review
│   └── jira_agent.py               # JIRA REST API — create/fetch/update/comment
│
├── models/
│   └── schemas.py                  # Pydantic v2 data contracts
│
├── utils/
│   └── logger.py                   # Rich terminal logging
│
├── templates/
│   └── index.html                  # Dark-theme web chat UI
│
├── requirements.txt
├── .env                            # Secrets (never committed)
└── .gitignore
```

---

## Tech Stack

| Component | Technology |
|---|---|
| LLM — Routing | Claude Haiku 4.5 (fast, cheap decisions) |
| LLM — Summarization / Code gen / Review | Claude Sonnet 4.6 |
| YouTube | `youtube-transcript-api` v0.6.x |
| GitHub | `PyGithub` |
| JIRA | `atlassian-python-api` + `requests` (REST v2) |
| Web UI | Flask + vanilla JS |
| Memory | SQLite (`sqlite3`) |
| Data Models | Pydantic v2 |
| Retry Logic | `tenacity` |
| Terminal Logging | `rich` |
| Config | `python-dotenv` |

---

## Setup

### Prerequisites
- Python 3.10+
- Anthropic API key → [console.anthropic.com](https://console.anthropic.com)
- GitHub Personal Access Token (Classic) with `repo` scope
- Atlassian API token → [id.atlassian.com → Security → API tokens](https://id.atlassian.com/manage-profile/security/api-tokens)

### Installation

```bash
git clone https://github.com/masandraju/youtube-summaries.git
cd youtube-summaries

pip install -r requirements.txt
```

### Configure `.env`

```env
# Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# GitHub
GITHUB_TOKEN=ghp_...
GITHUB_USERNAME=your_github_username
GITHUB_REPO_NAME=youtube-summaries

# JIRA
JIRA_BASE_URL=https://yoursite.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=ATATT3x...
JIRA_PROJECT_KEY=SCRUM
```

### Run (Web UI)

```bash
py app.py
```

Then open [http://localhost:5000](http://localhost:5000) in your browser.

### Run (CLI)

```bash
py main.py
```

---

## Usage Examples

### Summarize a YouTube video
```
https://www.youtube.com/watch?v=dQw4w9WgXcQ
```

### Full autonomous pipeline (new ticket + code + PR)
```
Create JIRA
```
The system asks what the ticket is about, then does everything automatically.

Or provide the description upfront to skip the question:
```
Create JIRA for implementing JWT authentication for the login endpoint
```

### Write code for an existing ticket
```
Write code for SCRUM-5
```

### Fetch a JIRA ticket
```
fetch SCRUM-42
```

### Update ticket status
```
Move SCRUM-42 to In Progress
```

### Push a summary to GitHub
Click the **Push to GitHub** button on any summary card, or type:
```
push summary_dQw4w9WgXcQ.md
```

### List saved summaries
```
list
```

### View task history
```
history
```

---

## How It Works

### 1. LLM-Based Routing
Every user message is sent to **Claude Haiku** which returns a structured JSON routing decision:
```json
{"agent": "jira", "action": "full_flow", "payload": {"summary": "Implement login"}, "confidence": 0.97}
```
This lets the system understand natural language instead of requiring exact command syntax.

### 2. Full Autonomous Pipeline Flow
```
"Create JIRA" →  _handle_full_autonomous_flow()
                 │
                 ├─ Extract ticket details (Claude Haiku)
                 ├─ Create JIRA ticket via REST API → get key
                 │
                 └─ _handle_code_flow(ticket_key)
                    │
                    ├─ Fetch ticket details from JIRA
                    ├─ Generate Python code (Claude Sonnet)
                    ├─ Create feature branch (PyGithub)
                    ├─ Push code file to branch
                    ├─ Run code review (Claude Sonnet) → score/10
                    ├─ If score ≥ 6 → Create Pull Request
                    ├─ Update JIRA status → "In Review"
                    └─ Add PR URL + review as JIRA comment
```

### 3. State Machine for Multi-Step Flows
When the orchestrator needs more info (e.g. ticket description), it saves state to SQLite (`app_state` table) and picks up where it left off on the next message — no session lost between requests.

### 4. Two Claude Models, Used Strategically
| Model | Task | Why |
|---|---|---|
| Claude Haiku 4.5 | Routing, transition picking, detail extraction | Fast and cheap for structured decisions |
| Claude Sonnet 4.6 | Summarization, code generation, code review | Best quality for heavy reasoning tasks |

---

## Design Patterns

| Pattern | Where |
|---|---|
| **Orchestrator** | `orchestrator.py` — central coordinator |
| **Template Method** | `base_agent.py` — `run()` wraps `_execute()` |
| **Strategy** | All agents are interchangeable via `agent.run(task)` |
| **Repository** | `memory.py` — abstracts all SQLite operations |
| **State Machine** | `app_state` table — multi-step flow persistence |
| **DTO** | Pydantic schemas — typed contracts between components |

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `GITHUB_TOKEN` | Yes | GitHub PAT with `repo` scope |
| `GITHUB_USERNAME` | Yes | Your GitHub @handle |
| `GITHUB_REPO_NAME` | No | Defaults to `youtube-summaries` |
| `JIRA_BASE_URL` | Yes | e.g. `https://yoursite.atlassian.net` |
| `JIRA_EMAIL` | Yes | Atlassian account email |
| `JIRA_API_TOKEN` | Yes | Atlassian API token |
| `JIRA_PROJECT_KEY` | No | Defaults to `SCRUM` |

---

## Security Notes

- `.env` is in `.gitignore` and never committed
- GitHub token uses minimum required scope (`repo` only)
- JIRA auth uses Atlassian API tokens (not your password)
- All secrets loaded via `python-dotenv` — never hardcoded
- Input validated via Pydantic before reaching any agent
- JIRA API calls go directly to REST v2 with Basic auth (email:token)

---

## License

MIT
