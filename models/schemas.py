"""
models/schemas.py
─────────────────
Pydantic data models — the contracts between Orchestrator and Agents.

WHY PYDANTIC?
  - Every message passed between agents is validated automatically
  - If a field is missing or wrong type → clear error immediately
  - Acts as documentation: you always know what shape data is in
  - Industry standard for Python APIs and agent systems
"""

from pydantic import BaseModel, HttpUrl, field_validator
from typing import Optional
from datetime import datetime
from enum import Enum


# ─────────────────────────────────────────────────────────────────────────────
# ENUMS — Fixed set of allowed values
# ─────────────────────────────────────────────────────────────────────────────

class TaskStatus(str, Enum):
    """Lifecycle of a task through the system."""
    PENDING    = "pending"      # Just received
    RUNNING    = "running"      # Agent is working on it
    COMPLETED  = "completed"    # Finished successfully
    FAILED     = "failed"       # Something went wrong


class AgentType(str, Enum):
    """Which agent handles a task."""
    YOUTUBE    = "youtube"
    GITHUB     = "github"
    UNKNOWN    = "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# TASK — The unit of work passed from Orchestrator → Agent
# ─────────────────────────────────────────────────────────────────────────────

class Task(BaseModel):
    """
    Represents a single unit of work routed to an agent.

    Flow:
      User Input → Orchestrator creates Task → routes to Agent → Agent fills result
    """
    task_id:     str                        # Unique identifier (UUID)
    agent:       AgentType                  # Which agent should handle this
    action:      str                        # What to do (e.g., "summarize", "push")
    payload:     dict                       # Input data for the agent
    status:      TaskStatus = TaskStatus.PENDING
    result:      Optional[dict] = None      # Agent fills this on completion
    error:       Optional[str] = None       # Filled if status = FAILED
    created_at:  datetime = datetime.now()
    completed_at: Optional[datetime] = None


# ─────────────────────────────────────────────────────────────────────────────
# YOUTUBE AGENT — Input and Output shapes
# ─────────────────────────────────────────────────────────────────────────────

class YouTubeInput(BaseModel):
    """What the YouTube Agent receives."""
    url: str                                # Full YouTube URL

    @field_validator('url')
    @classmethod
    def validate_youtube_url(cls, v: str) -> str:
        """Ensure it's actually a YouTube URL before doing any work."""
        if 'youtube.com' not in v and 'youtu.be' not in v:
            raise ValueError("URL must be a YouTube link (youtube.com or youtu.be)")
        return v


class TechnicalSummary(BaseModel):
    """
    Structured summary produced by the YouTube Agent after LLM processing.
    This exact structure is what gets saved to GitHub as a markdown file.
    """
    video_id:       str                     # YouTube video ID (e.g., dQw4w9WgXcQ)
    video_url:      str                     # Original URL
    title:          str                     # Video title (extracted by LLM)
    overview:       str                     # 2-3 sentence overview
    key_points:     list[str]               # Main takeaways as bullet points
    technical_concepts: list[str]           # Technologies/concepts mentioned
    code_snippets:  list[str]               # Any code examples mentioned
    tools_mentioned: list[str]              # Libraries, tools, frameworks mentioned
    target_audience: str                    # Who is this video for?
    difficulty_level: str                   # Beginner / Intermediate / Advanced
    transcript_length: int                  # Number of words in transcript
    generated_at:   datetime = datetime.now()


# ─────────────────────────────────────────────────────────────────────────────
# GITHUB AGENT — Input and Output shapes
# ─────────────────────────────────────────────────────────────────────────────

class GitHubPushInput(BaseModel):
    """What the GitHub Agent needs to push a file."""
    filename:    str                        # e.g., "summary_dQw4w9WgXcQ.md"
    content:     str                        # File content (markdown)
    commit_message: str                     # Git commit message


class GitHubFetchInput(BaseModel):
    """What the GitHub Agent needs to fetch a file."""
    filename:    str                        # File to retrieve


class GitHubResult(BaseModel):
    """What the GitHub Agent returns after push/fetch."""
    success:     bool
    action:      str                        # "pushed" or "fetched"
    filename:    str
    url:         Optional[str] = None       # GitHub URL to the file
    content:     Optional[str] = None       # Filled on fetch
    message:     str                        # Human-readable result


# ─────────────────────────────────────────────────────────────────────────────
# ORCHESTRATOR — Internal routing model
# ─────────────────────────────────────────────────────────────────────────────

class RoutingDecision(BaseModel):
    """
    What the LLM (Claude) returns when deciding how to handle user input.
    The Orchestrator sends user input to Claude, Claude fills this model.
    """
    agent:       AgentType                  # Which agent to call
    action:      str                        # What action to perform
    payload:     dict                       # Extracted parameters from user input
    confidence:  float                      # 0.0 to 1.0 — how sure Claude is
    reasoning:   str                        # Why Claude made this decision
