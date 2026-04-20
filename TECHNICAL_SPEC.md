# DevFlow AI — Technical Specification

**Version:** 1.0.0  
**Last Updated:** April 2026  
**Author:** Raju Masand  
**Stack:** Python 3.10+ · Flask · Claude Sonnet 4.6 · SQLite · GitHub API · JIRA REST API

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture](#2-architecture)
3. [Component Specifications](#3-component-specifications)
   - 3.1 [Orchestrator](#31-orchestrator)
   - 3.2 [YouTube Agent](#32-youtube-agent)
   - 3.3 [GitHub Agent](#33-github-agent)
   - 3.4 [JIRA Agent](#34-jira-agent)
   - 3.5 [Base Agent](#35-base-agent)
   - 3.6 [Memory Layer](#36-memory-layer)
4. [Data Models](#4-data-models)
5. [Database Schema](#5-database-schema)
6. [API Reference](#6-api-reference)
7. [LLM Routing System](#7-llm-routing-system)
8. [Autonomous Pipeline](#8-autonomous-pipeline)
9. [State Machine](#9-state-machine)
10. [Configuration & Environment](#10-configuration--environment)
11. [Error Handling & Retry Strategy](#11-error-handling--retry-strategy)
12. [Security Model](#12-security-model)
13. [Design Patterns](#13-design-patterns)
14. [Dependencies](#14-dependencies)
15. [Sequence Diagrams](#15-sequence-diagrams)

---

## 1. System Overview

DevFlow AI is a multi-agent orchestration system that connects three external platforms — YouTube, GitHub, and JIRA — through a single natural language interface. It uses Anthropic's Claude LLM for both routing decisions and content generation.

### Core Capabilities

| Capability | Description |
|---|---|
| YouTube summarization | Fetches transcript, generates structured technical summary via Claude |
| GitHub file management | Push/fetch/list Markdown files in a GitHub repository |
| GitHub code pipeline | Create branch → push generated code → open PR → code review |
| JIRA ticket management | Create, fetch, update status, add comments, link PRs |
| Full autonomous pipeline | Create JIRA ticket + entire code pipeline in one command |
| Natural language routing | Claude Haiku interprets free-form input to decide which agent to call |
| Persistent memory | SQLite stores all task history, summaries, and JIRA tickets |

### Key Design Decisions

- **Two LLM models deliberately**: Claude Haiku for fast routing decisions (low latency, low cost), Claude Sonnet for heavy generation tasks (summarization, code, review).
- **No JIRA SDK writes**: JIRA create/comment calls use `requests` directly against REST v2 to get clear HTTP error bodies. Read operations still use `atlassian-python-api`.
- **SQLite over a hosted DB**: Zero infrastructure dependency, file-based persistence, sufficient for single-user orchestrator.
- **State machine in DB**: Multi-step flows (mid-creation Q&A) are persisted in SQLite `app_state` table, surviving web server restarts.

---

## 2. Architecture

### High-Level Component Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                         WEB BROWSER                                 │
│              DevFlow AI Chat UI (index.html)                        │
│         Dark-theme chat · Typing indicator · Rich cards             │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ HTTP POST /api/chat
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      FLASK WEB SERVER (app.py)                      │
│        Single Orchestrator instance · Stateless request handler     │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    ORCHESTRATOR (orchestrator.py)                   │
│                                                                     │
│  1. Check pending state (pending_full_flow / pending_jira)          │
│  2. Check built-in commands (list, history, help)                   │
│  3. Send input to Claude Haiku → get RoutingDecision                │
│  4. Dispatch Task to the correct Agent                              │
│  5. Return structured dict to Flask                                 │
└────────┬──────────────────┬─────────────────────┬───────────────────┘
         │                  │                     │
         ▼                  ▼                     ▼
┌────────────────┐ ┌─────────────────┐ ┌──────────────────┐
│ YOUTUBE AGENT  │ │  GITHUB AGENT   │ │   JIRA AGENT     │
│                │ │                 │ │                  │
│ youtube-       │ │ PyGithub        │ │ atlassian-python │
│ transcript-api │ │ REST API        │ │ -api + requests  │
│ Claude Sonnet  │ │ Claude Sonnet   │ │ JIRA REST v2     │
└───────┬────────┘ └────────┬────────┘ └────────┬─────────┘
        │                   │                   │
        └───────────────────┼───────────────────┘
                            ▼
              ┌─────────────────────────┐
              │   MEMORY (memory.py)    │
              │   SQLite — 5 tables     │
              │   tasks · summaries     │
              │   session_log           │
              │   app_state             │
              │   jira_tickets          │
              └─────────────────────────┘
```

### External Service Dependencies

```
DevFlow AI
    ├── Anthropic API       → Claude Haiku 4.5 (routing)
    │                       → Claude Sonnet 4.6 (summarize / code / review)
    ├── YouTube             → youtube-transcript-api (no API key required)
    ├── GitHub REST API     → PyGithub (PAT authentication)
    └── JIRA Cloud REST v2  → requests (Basic auth: email:token)
```

---

## 3. Component Specifications

### 3.1 Orchestrator

**File:** `orchestrator/orchestrator.py`  
**Class:** `Orchestrator`

The central controller. Owns all agents and the Memory instance. Implements two public interfaces: `handle()` for CLI, `handle_web()` for the Flask web server.

#### Responsibilities

| Responsibility | Implementation |
|---|---|
| State machine check | Reads `app_state` table before routing |
| LLM-based routing | Calls Claude Haiku with routing prompt, parses JSON |
| Task dispatch | Creates `Task` object, calls `agent.run(task)` |
| Multi-step pipelines | `_handle_code_flow()`, `_handle_full_autonomous_flow()` |
| Response shaping | Returns typed dicts (`type`, `message`, `data`) for the web UI |

#### Routing Actions Table

| Input Pattern | Routed To | Method |
|---|---|---|
| YouTube URL | `youtube → summarize` | `YouTubeAgent._execute()` |
| "push filename.md" | `github → push` | `GitHubAgent._push()` |
| "fetch filename.md" | `github → fetch` | `GitHubAgent._fetch()` |
| "list" / "ls" | built-in | `Memory.list_summaries()` |
| "fetch SCRUM-42" | `jira → fetch` | `JiraAgent._fetch_ticket()` |
| "move SCRUM-42 to Done" | `jira → update_status` | `JiraAgent._update_status()` |
| "write code for SCRUM-42" | `github → write_code` | `_handle_code_flow()` |
| "create jira" / "new ticket" | `jira → full_flow` | `_handle_full_autonomous_flow()` |

#### Key Methods

```
handle_web(user_input)              → dict
  ├── check pending_full_flow state
  ├── check pending_jira state
  ├── handle built-in commands
  ├── _route_with_llm(user_input)   → RoutingDecision
  └── dispatch to agent or pipeline

_route_with_llm(user_input)        → RoutingDecision
  └── Claude Haiku → JSON → RoutingDecision

_handle_full_autonomous_flow()     → dict
  └── _create_ticket_then_pipeline()
        ├── JiraAgent.create_ticket()
        └── _handle_code_flow(ticket_key)

_handle_code_flow(ticket_key)      → dict
  ├── JiraAgent.fetch_ticket()
  ├── _generate_code()              → (code, filename)
  ├── GitHubAgent.create_branch()
  ├── GitHubAgent.push_code()
  ├── GitHubAgent.code_review()     → score/10
  ├── if score ≥ 6 → GitHubAgent.create_pr()
  ├── if PR created → JiraAgent.update_status("In Review")
  └── JiraAgent.add_comment(PR URL + review)

_generate_code(ticket_key, summary, description) → (str, str)
  └── Claude Sonnet → JSON {filename, code}

_resume_full_autonomous_flow(user_input) → dict
  └── Claude Haiku extracts ticket details → _create_ticket_then_pipeline()
```

---

### 3.2 YouTube Agent

**File:** `agents/youtube_agent.py`  
**Class:** `YouTubeAgent`

#### Flow

```
1. Validate URL (Pydantic YouTubeInput validator)
2. Extract video_id via regex from URL
3. Check SQLite summary cache → return immediately if hit
4. Fetch transcript via youtube-transcript-api v0.6.x
   ├── Primary: api.fetch(video_id)
   └── Fallback: api.list(video_id) → iterate transcripts
5. Concatenate transcript segments → single text block
6. Send to Claude Sonnet with structured JSON prompt
7. Parse Claude response → TechnicalSummary model
8. Cache in SQLite
9. Return TechnicalSummary dict
```

#### LLM Prompt Strategy

Claude Sonnet is given a system prompt requiring it to return a strict JSON object:

```json
{
  "title": "string",
  "overview": "string (2-3 sentences)",
  "key_points": ["string", ...],
  "technical_concepts": ["string", ...],
  "code_snippets": ["string", ...],
  "tools_mentioned": ["string", ...],
  "target_audience": "string",
  "difficulty_level": "Beginner|Intermediate|Advanced"
}
```

Transcript is trimmed to 12,000 characters to stay within token limits.

#### Supported URL Formats

| Format | Example |
|---|---|
| Standard watch | `https://www.youtube.com/watch?v=VIDEO_ID` |
| Short URL | `https://youtu.be/VIDEO_ID` |
| With timestamp | `https://www.youtube.com/watch?v=VIDEO_ID&t=120s` |
| Embed URL | `https://www.youtube.com/embed/VIDEO_ID` |

---

### 3.3 GitHub Agent

**File:** `agents/github_agent.py`  
**Class:** `GitHubAgent`  
**Auth:** GitHub Personal Access Token (PAT Classic, `repo` scope)

#### Actions

| Action | Method | Description |
|---|---|---|
| `push` | `_push(payload)` | Create or update a file in the repo |
| `fetch` | `_fetch(payload)` | Retrieve file content (base64 decoded) |
| `list` | `_list_files()` | List all files in repo root |
| `create_branch` | `_create_branch(payload)` | Create feature branch from main |
| `push_code` | `_push_code(payload)` | Commit a generated code file to a branch |
| `create_pr` | `_create_pr(payload)` | Open a Pull Request (branch → main) |
| `code_review` | `_code_review(payload)` | Claude reviews code, returns structured feedback |

#### Code Review Output Schema

```json
{
  "overall": "1-2 sentence assessment",
  "score": 7,
  "quality_issues": ["issue 1", "issue 2"],
  "security_concerns": ["concern 1"],
  "suggestions": ["suggestion 1"],
  "positive_aspects": ["good thing 1"],
  "jira_comment": "formatted text for JIRA comment"
}
```

Score threshold for PR creation: **≥ 6 / 10**

#### Branch Naming Convention

```
feature/{ticket-key-lowercase}-{kebab-case-summary-slug}

Examples:
  feature/scrum-5-implement-jwt-authentication
  feature/scrum-12-add-user-profile-endpoint
```

Slug is generated from the ticket summary, lowercased, non-alphanumeric characters replaced with `-`, truncated to 40 characters.

#### Retry Strategy

Push and fetch operations use `tenacity` with:
- Max attempts: 3
- Wait: exponential backoff, 1s minimum, 5s maximum

---

### 3.4 JIRA Agent

**File:** `agents/jira_agent.py`  
**Class:** `JiraAgent`  
**Auth:** Basic auth — `email:api_token` (Base64 encoded by `requests`)  
**API Version:** JIRA Cloud REST v2 (`/rest/api/2/`)

#### Actions

| Action | Method | Description |
|---|---|---|
| `ask_create` | `_ask_create_questions()` | Returns question schema for the web form |
| `create` | `_create_ticket(payload)` | Creates ticket via REST v2 POST |
| `fetch` | `_fetch_ticket(payload)` | Gets ticket details, extracts ADF description |
| `update_status` | `_update_status(payload)` | Transitions ticket via named workflow transition |
| `add_comment` | `_add_comment(payload)` | Posts plain-text comment via REST v2 |
| `update_pr` | `_update_pr(payload)` | Adds PR URL + review summary as comment |

#### Authentication Pre-flight

Before every create/comment operation, `_check_auth()` calls `/rest/api/2/myself` to verify credentials. A `401` response raises an actionable error message with a link to regenerate the token.

#### ADF Description Extraction

JIRA Cloud returns descriptions in Atlassian Document Format (ADF) — a nested JSON tree. The `_extract_text_from_adf()` method recursively walks the tree extracting `text` nodes:

```python
def walk(node):
    if node.get("type") == "text":
        texts.append(node.get("text", ""))
    for child in node.get("content", []):
        walk(child)
```

#### Transition Matching

`_update_status()` fetches available workflow transitions from JIRA, then:
1. Tries exact case-insensitive match
2. If no match → sends transition list to Claude Haiku to pick the closest match
3. Safety check ensures the returned name is actually in the list

---

### 3.5 Base Agent

**File:** `agents/base_agent.py`  
**Class:** `BaseAgent` (abstract)

Implements the **Template Method** pattern. All agents inherit from it.

```python
def run(self, task: Task) -> Task:
    """Template method — not overridden by subclasses."""
    self.memory.update_task_status(task.task_id, "running")
    try:
        result = self._execute(task)          # ← subclass implements this
        task.result = result
        task.status = TaskStatus.COMPLETED
        self.memory.update_task_status(task.task_id, "completed", result=result)
    except Exception as e:
        task.status = TaskStatus.FAILED
        task.error  = str(e)
        self.memory.update_task_status(task.task_id, "failed", error=str(e))
    return task

@abstractmethod
def _execute(self, task: Task) -> dict:
    """Each agent implements its own logic here."""
```

This guarantees that:
- Every task is logged as `running` when it starts
- Every task is logged as `completed` or `failed` regardless of what the agent does
- Error messages are always captured in the database
- No agent can forget to update task status

---

### 3.6 Memory Layer

**File:** `orchestrator/memory.py`  
**Class:** `Memory`  
**Backend:** SQLite (file: `orchestrator_memory.db`)

#### Design

- Single `sqlite3.Connection` shared across all agents (`check_same_thread=False`)
- `row_factory = sqlite3.Row` — rows behave like dicts with column-name access
- All JSON blobs stored as TEXT, serialized/deserialized with `json.dumps/loads`
- All timestamps stored as ISO 8601 strings

#### Key Behaviors

| Behavior | Detail |
|---|---|
| Summary cache | `ON CONFLICT DO UPDATE` — same video never re-processes |
| App state | `ON CONFLICT DO UPDATE` — state is always the latest value |
| JIRA cache | `ON CONFLICT DO UPDATE` — tickets updated in-place |
| Task log | Append-only — historical record never modified |
| Session log | Append-only — full conversation history |

---

## 4. Data Models

All inter-component contracts are defined as **Pydantic v2** models in `models/schemas.py`. Validation is automatic — any agent receiving malformed data gets an immediate, clear error.

### Task

```python
class Task(BaseModel):
    task_id:      str           # UUID4
    agent:        AgentType     # youtube | github | jira | unknown
    action:       str           # e.g. "summarize", "push", "full_flow"
    payload:      dict          # Agent-specific input data
    status:       TaskStatus    # pending → running → completed | failed
    result:       Optional[dict]
    error:        Optional[str]
    created_at:   datetime
    completed_at: Optional[datetime]
```

### RoutingDecision

```python
class RoutingDecision(BaseModel):
    agent:      AgentType   # Which agent to call
    action:     str         # Which action to perform
    payload:    dict        # Parameters extracted from user input
    confidence: float       # 0.0 – 1.0
    reasoning:  str         # Claude's explanation
```

### TechnicalSummary

```python
class TechnicalSummary(BaseModel):
    video_id:           str
    video_url:          str
    title:              str
    overview:           str
    key_points:         list[str]
    technical_concepts: list[str]
    code_snippets:      list[str]
    tools_mentioned:    list[str]
    target_audience:    str
    difficulty_level:   str       # Beginner | Intermediate | Advanced
    transcript_length:  int
    generated_at:       datetime
```

### JiraTicketInput

```python
class JiraTicketInput(BaseModel):
    summary:     str
    issue_type:  str = "Story"    # Story | Bug | Task | Epic
    priority:    str = "Medium"   # Highest | High | Medium | Low
    description: str = ""
```

### GitHubResult

```python
class GitHubResult(BaseModel):
    success:  bool
    action:   str             # "pushed" | "fetched"
    filename: str
    url:      Optional[str]   # GitHub file URL
    content:  Optional[str]   # Populated on fetch
    message:  str
```

---

## 5. Database Schema

**File:** `orchestrator_memory.db` (SQLite)

### tasks

```sql
CREATE TABLE tasks (
    task_id      TEXT PRIMARY KEY,       -- UUID4
    agent        TEXT NOT NULL,          -- youtube | github | jira
    action       TEXT NOT NULL,          -- summarize | push | create | etc.
    payload      TEXT NOT NULL,          -- JSON blob
    status       TEXT NOT NULL,          -- pending | running | completed | failed
    result       TEXT,                   -- JSON blob (null until complete)
    error        TEXT,                   -- Error message (null unless failed)
    created_at   TEXT NOT NULL,          -- ISO 8601
    completed_at TEXT                    -- ISO 8601 (null until done)
);
```

### summaries

```sql
CREATE TABLE summaries (
    video_id     TEXT PRIMARY KEY,       -- YouTube video ID
    video_url    TEXT NOT NULL,          -- Full YouTube URL
    summary_json TEXT NOT NULL,          -- TechnicalSummary as JSON
    github_url   TEXT,                   -- GitHub URL after push (nullable)
    created_at   TEXT NOT NULL
);
```

### session_log

```sql
CREATE TABLE session_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    role       TEXT NOT NULL,            -- "user" | "assistant"
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

### app_state

```sql
CREATE TABLE app_state (
    key        TEXT PRIMARY KEY,         -- e.g. "pending_full_flow"
    value      TEXT NOT NULL,            -- JSON blob
    updated_at TEXT NOT NULL
);
```

Tracked states:

| Key | Value Shape | Purpose |
|---|---|---|
| `pending_full_flow` | `{"step": "awaiting_summary"}` | User typed "Create JIRA", waiting for description |
| `pending_jira` | `{"step": "awaiting_answers", "questions": {...}}` | Mid JIRA Q&A form flow |

### jira_tickets

```sql
CREATE TABLE jira_tickets (
    ticket_key  TEXT PRIMARY KEY,        -- e.g. SCRUM-42
    summary     TEXT NOT NULL,
    issue_type  TEXT,
    priority    TEXT,
    status      TEXT,
    description TEXT,
    url         TEXT,
    pr_url      TEXT,                    -- GitHub PR URL (nullable)
    created_at  TEXT NOT NULL
);
```

---

## 6. API Reference

### Flask Endpoints

**File:** `app.py`

#### `GET /`
Returns the web chat UI (`templates/index.html`).

---

#### `POST /api/chat`

Main entry point. Accepts a user message, returns a typed response dict.

**Request:**
```json
{
  "message": "Create JIRA for implementing user authentication"
}
```

**Response shape:**
```json
{
  "type": "code_flow | summary | github | jira | jira_questions | text | error | list | history | help",
  "message": "Human-readable status message",
  "data": { }
}
```

**Response types:**

| `type` | When returned | `data` contents |
|---|---|---|
| `summary` | YouTube summary generated | `TechnicalSummary` fields + `push_hint` |
| `github` | GitHub push/fetch completed | `action`, `filename`, `url`, `content` |
| `jira` | JIRA ticket created/fetched/updated | `key`, `summary`, `status`, `url`, `pr_url` |
| `jira_questions` | `ask_create` action — form rendering | `questions` dict with field metadata |
| `code_flow` | Full pipeline completed | `ticket_key`, `branch_name`, `pr_url`, `review`, `steps`, `code_preview` |
| `text` | Plain message (e.g. one-question prompt) | empty |
| `error` | Any agent failure | empty |
| `list` | Summary list | `summaries` array |
| `history` | Task history | `tasks` array |
| `help` | Help command | `commands` array |

---

#### `GET /api/history`

Returns the 20 most recent tasks from the `tasks` table.

**Response:**
```json
[
  {
    "task_id": "uuid",
    "agent": "jira",
    "action": "create",
    "status": "completed",
    "created_at": "2026-04-20T10:00:00"
  }
]
```

---

#### `GET /api/summaries`

Returns all cached video summaries.

**Response:**
```json
[
  {
    "video_id": "dQw4w9WgXcQ",
    "video_url": "https://youtube.com/watch?v=...",
    "github_url": "https://github.com/.../summary_dQw4w9WgXcQ.md",
    "created_at": "2026-04-20T10:00:00"
  }
]
```

---

## 7. LLM Routing System

The routing system is the brain of the orchestrator. It uses **Claude Haiku 4.5** (fast, cheap) to interpret free-form user input and return a structured `RoutingDecision`.

### Why LLM Routing vs. if/else

| Approach | "push the last summary" | "can u upload it 2 github?" | "push it" |
|---|---|---|---|
| Regex/if-else | ✅ | ❌ | ❌ |
| LLM Routing | ✅ | ✅ | ✅ |

### Routing Prompt Design

The system prompt gives Claude Haiku a menu of available agent/action pairs with precise descriptions of when to use each. Key rules:

- `jira → full_flow` is the ONLY action for "create ticket" requests — prevents ambiguity with the old `create` action
- `github → write_code` is for existing tickets by key only
- `unknown → none` is the safe fallback

### Response Format

Claude Haiku must return pure JSON (no markdown fences):

```json
{
  "agent": "jira",
  "action": "full_flow",
  "payload": {"summary": "Implement JWT authentication"},
  "confidence": 0.96,
  "reasoning": "User asked to create a JIRA ticket with a description"
}
```

### Markdown Fence Stripping

Claude occasionally wraps JSON in code fences despite instructions. The orchestrator strips them:

```python
if raw.startswith("```"):
    raw = raw.split("```")[1]
    if raw.startswith("json"):
        raw = raw[4:]
    raw = raw.strip()
```

---

## 8. Autonomous Pipeline

The full autonomous pipeline is the flagship feature. A single "Create JIRA" message triggers a 7-step sequential workflow with no user intervention.

### Entry Points

| Trigger | Path |
|---|---|
| "Create JIRA" (no description) | Router → `full_flow` → `_handle_full_autonomous_flow()` → asks ONE question → `_resume_full_autonomous_flow()` → pipeline |
| "Create JIRA for X" (with description) | Router extracts summary → `full_flow` → `_handle_full_autonomous_flow()` → pipeline immediately |
| "Write code for SCRUM-X" | Router → `write_code` → `_handle_code_flow(ticket_key)` — skips ticket creation |

### Pipeline Steps

```
Step 0  JIRA ticket created         JiraAgent._create_ticket()
        └─ Returns ticket_key (e.g. SCRUM-7)

Step 1  Fetch ticket details         JiraAgent._fetch_ticket()
        └─ Gets summary + description for code generation context

Step 2  Generate code                Orchestrator._generate_code()
        └─ Claude Sonnet → JSON {filename, code}
        └─ max_tokens: 3000

Step 3  Create feature branch        GitHubAgent._create_branch()
        └─ branch: feature/{key-lower}-{slug}
        └─ based off: main (fallback: master)

Step 4  Push code to branch          GitHubAgent._push_code()
        └─ path: code/{filename}
        └─ commit message: "{TICKET_KEY}: {summary}"

Step 5  Code review                  GitHubAgent._code_review()
        └─ Claude Sonnet analyzes code
        └─ Returns score (1-10) + structured feedback

Step 6  Create Pull Request          GitHubAgent._create_pr()  [conditional]
        └─ Only if review score ≥ 6
        └─ PR body includes review score + feedback
        └─ If score < 6: posts review as JIRA comment, skips PR

Step 7  Update JIRA                  JiraAgent._update_status() + _update_pr()
        └─ Transition ticket → "In Review"
        └─ Add comment: PR URL + full code review text
```

### Result Card Fields

```json
{
  "type": "code_flow",
  "data": {
    "ticket_key":     "SCRUM-7",
    "ticket_summary": "Implement health check endpoint",
    "ticket_url":     "https://site.atlassian.net/browse/SCRUM-7",
    "branch_name":    "feature/scrum-7-implement-health-check",
    "filename":       "code/health_check.py",
    "pr_url":         "https://github.com/user/repo/pull/2",
    "pr_number":      2,
    "review": {
      "score": 8,
      "overall": "Clean, well-structured implementation.",
      "quality_issues": [],
      "security_concerns": [],
      "suggestions": ["Add rate limiting"],
      "positive_aspects": ["Good error handling"]
    },
    "steps": [
      {"step": "JIRA ticket created", "detail": "SCRUM-7 — ..."},
      {"step": "Code generated",      "detail": "health_check.py"},
      ...
    ],
    "code_preview": "first 800 chars of generated code"
  }
}
```

---

## 9. State Machine

Multi-step conversation flows are managed via the `app_state` SQLite table. This allows the web server to be stateless — all in-progress state survives restarts.

### State Transitions

```
full_flow (no description provided):

  User: "Create JIRA"
       │
       ▼
  handle_web() → router → full_flow
       │
       ▼
  _handle_full_autonomous_flow()
  → summary is empty
  → set_state("pending_full_flow", {"step": "awaiting_summary"})
  → return question prompt to user
       │
       ▼ (next request)
  handle_web()
  → get_state("pending_full_flow") → {"step": "awaiting_summary"}
  → clear_state("pending_full_flow")
  → _resume_full_autonomous_flow(user_answer)
  → extract ticket details (Claude Haiku)
  → _create_ticket_then_pipeline()
  → return code_flow result
```

### State Keys Reference

| Key | Set By | Cleared By | Purpose |
|---|---|---|---|
| `pending_full_flow` | `_handle_full_autonomous_flow()` | `_resume_full_autonomous_flow()` | Waiting for ticket description |
| `pending_jira` | `JiraAgent._ask_create_questions()` | `JiraAgent._create_ticket()` | Mid JIRA Q&A form |

---

## 10. Configuration & Environment

All secrets are loaded via `python-dotenv` from a `.env` file at project root. Never hardcoded.

### Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key for Claude |
| `GITHUB_TOKEN` | Yes | — | GitHub PAT (Classic) with `repo` scope |
| `GITHUB_USERNAME` | Yes | — | GitHub @handle (not email) |
| `GITHUB_REPO_NAME` | No | `youtube-summaries` | Target GitHub repository |
| `JIRA_BASE_URL` | Yes | — | e.g. `https://yoursite.atlassian.net` |
| `JIRA_EMAIL` | Yes | — | Atlassian account email |
| `JIRA_API_TOKEN` | Yes | — | Atlassian API token |
| `JIRA_PROJECT_KEY` | No | `SCRUM` | JIRA project key |

### JIRA API Token

Token must be generated at:  
`https://id.atlassian.com/manage-profile/security/api-tokens`

The email used for `JIRA_EMAIL` **must match** the Atlassian account that generated the token. Tokens do not expire unless manually revoked.

---

## 11. Error Handling & Retry Strategy

### Retry Policy (Tenacity)

Applied to GitHub push and fetch operations:

```python
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=5))
```

| Attempt | Wait Before |
|---|---|
| 1st | 0s |
| 2nd | ~1s |
| 3rd | ~2–5s |

JIRA create/comment operations do **not** use retry — a `400/401/403` from JIRA is a deterministic error (bad credentials, bad fields) and retrying makes no sense.

### Error Surface Strategy

| Layer | How Errors Are Handled |
|---|---|
| Pydantic validation | Raises `ValidationError` immediately — caught by `BaseAgent.run()` |
| Agent execution | All exceptions caught in `BaseAgent.run()`, stored in `task.error` |
| JIRA HTTP errors | Raw response body parsed and re-raised as `RuntimeError` with readable message |
| GitHub API errors | `GithubException` / `UnknownObjectException` propagated to base agent |
| LLM routing parse failure | Returns `AgentType.UNKNOWN` → safe fallback message to user |
| Auth pre-flight failure | `_check_auth()` raises `RuntimeError` with token regeneration instructions |

### Web Error Response

Any agent failure returns:

```json
{
  "type": "error",
  "message": "Specific, actionable error message here",
  "data": {}
}
```

---

## 12. Security Model

### Secrets Management

- All credentials in `.env`, excluded from git via `.gitignore`
- No secrets hardcoded anywhere in source
- `python-dotenv` loads at startup, values accessed via `os.getenv()`
- JIRA agent strips whitespace from all credential values to prevent hidden-character auth failures

### GitHub Token Scope

Minimum required scope: `repo` (full control of private repositories).  
This grants: read/write files, create branches, create pull requests.

### JIRA Authentication

Uses HTTP Basic auth: `base64(email:api_token)`. The API token is not the account password — it's a separate, revocable credential.

### Input Validation

All agent inputs are validated through Pydantic models before any external call is made. Invalid inputs fail fast with a clear error rather than propagating garbage to external APIs.

### No Eval / No Shell Injection

Generated code from Claude is committed to GitHub as a file. It is never executed locally.

---

## 13. Design Patterns

| Pattern | Where Used | Why |
|---|---|---|
| **Orchestrator** | `Orchestrator` class | Single coordination point — agents don't talk to each other directly |
| **Template Method** | `BaseAgent.run()` wraps `_execute()` | Guarantees consistent status tracking regardless of agent implementation |
| **Strategy** | All agents implement `run(task) → Task` | Agents are interchangeable — orchestrator doesn't know their internals |
| **Repository** | `Memory` class | All data access behind one interface — swap SQLite for Postgres without touching agents |
| **State Machine** | `app_state` table | Multi-step flows survive server restarts, no in-memory session needed |
| **DTO (Data Transfer Object)** | Pydantic models | Typed, validated contracts between every component boundary |
| **Factory** | `Orchestrator.__init__` creates all agents | Single place to change agent configuration |

---

## 14. Dependencies

### `requirements.txt`

```
anthropic>=0.40.0          # Claude API client
youtube-transcript-api>=0.6.2  # YouTube transcript (instance-based API in v0.6.x)
PyGithub>=2.3.0            # GitHub REST API wrapper
pydantic>=2.7.0            # Data validation and serialization
python-dotenv>=1.0.0       # .env file loading
rich>=13.7.0               # Terminal color logging
tenacity>=8.3.0            # Retry with exponential backoff
flask>=3.0.0               # Web server
atlassian-python-api>=3.41.0  # JIRA read operations
requests>=2.31.0           # JIRA write operations (direct REST v2)
```

### Python Version

Minimum: **Python 3.10**  
Required for: `match/case`, `list[str]` type hints (no `List` import needed), `dict | None` union syntax.

### Notable Compatibility Note

`youtube-transcript-api` v0.6.x introduced a breaking change — `get_transcript()` and `list_transcripts()` are no longer class methods. The agent uses the new instance-based pattern:

```python
api = YouTubeTranscriptApi()
transcript = api.fetch(video_id)
```

---

## 15. Sequence Diagrams

### YouTube Summary Flow

```
User          Flask        Orchestrator    YouTubeAgent    Claude Sonnet    SQLite
 │              │               │               │                │             │
 │─ POST /chat ▶│               │               │                │             │
 │              │─ handle_web()▶│               │                │             │
 │              │               │─ route(input)─────────────────────────────────▶ Haiku
 │              │               │◀── {youtube, summarize, url} ──────────────────│
 │              │               │─ agent.run(task) ─▶│               │             │
 │              │               │               │─ check cache ────────────────────▶│
 │              │               │               │◀─ cache miss ───────────────────│
 │              │               │               │─ fetch transcript ─▶            │
 │              │               │               │─ send to Claude ──────────────▶│
 │              │               │               │◀── TechnicalSummary JSON ──────│
 │              │               │               │─ cache result ───────────────────▶│
 │              │               │◀── result dict─│               │             │
 │              │◀── {type:summary, data:...} ──│               │             │
 │◀─ response ──│               │               │               │             │
```

### Full Autonomous Pipeline Flow

```
User       Orchestrator     JiraAgent    GitHubAgent    Claude     JIRA API    GitHub API
 │              │               │              │           │            │            │
 │─"Create JIRA"▶│               │              │           │            │            │
 │              │─ route ──────────────────────────────────▶ Haiku                   │
 │              │◀─ {jira, full_flow, {}} ─────────────────│                         │
 │              │─ no summary in payload                    │                         │
 │              │─ set_state(pending_full_flow)              │                         │
 │◀─ "What should the ticket be about?" ─────────────────────────────────────────────│
 │              │               │              │           │            │            │
 │─ "Implement JWT auth" ────────────────────────────────────────────────────────────▶│
 │              │─ get_state(pending_full_flow) → hit       │                         │
 │              │─ extract ticket details ─────────────────▶ Haiku                   │
 │              │◀─ {summary, issue_type, priority} ────────│                         │
 │              │─ create_task(jira, create) ───▶│          │                         │
 │              │               │─ POST /rest/api/2/issue ──────────────▶│            │
 │              │               │◀─ {key: SCRUM-7} ─────────────────────│            │
 │              │◀─ {key: SCRUM-7} ─────────────│          │            │            │
 │              │─ fetch ticket ────────────────▶│          │            │            │
 │              │◀─ ticket details ──────────────│          │            │            │
 │              │─ generate code ──────────────────────────▶ Sonnet                  │
 │              │◀─ {filename, code} ────────────────────────│                        │
 │              │─ create_branch ────────────────────────────────────────▶│           │
 │              │─ push_code ────────────────────────────────────────────▶│           │
 │              │─ code_review ───────────────────────────▶ Sonnet                   │
 │              │◀─ {score:8, issues:[], ...} ───────────────│                        │
 │              │ score ≥ 6                      │            │                        │
 │              │─ create_pr ────────────────────────────────────────────▶│           │
 │              │◀─ {pr_url, pr_number} ─────────────────────────────────│           │
 │              │─ update_status(In Review) ────▶│           │            │            │
 │              │─ add_comment(PR URL + review) ▶│           │            │            │
 │◀─ {type: code_flow, data: {steps, pr_url, review, ...}} ─────────────────────────│
```

---

*This document covers the full technical specification of DevFlow AI as of v1.0.0. For setup instructions and usage examples see [README.md](README.md).*
