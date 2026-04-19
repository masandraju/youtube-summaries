"""
orchestrator/orchestrator.py
─────────────────────────────
The Orchestrator — the central controller of the entire system.

RESPONSIBILITIES:
  1. Receive raw user input (string)
  2. Send input to Claude → Claude decides which agent to call and with what params
  3. Create a Task and dispatch it to the correct agent
  4. Handle the agent's result and respond to the user
  5. Maintain conversation context in Memory

WHY LLM-BASED ROUTING?
  Traditional routers use if/else or regex to match commands.
  LLM routing is smarter:
    - "can you push the last summary" → GitHub push
    - "summarize this: youtu.be/xyz" → YouTube summarize
    - "what did i process last?" → Memory list
  Natural language is understood, not just exact commands.
"""

import os
import json
import uuid
from anthropic import Anthropic

from orchestrator.memory import Memory
from agents.youtube_agent import YouTubeAgent
from agents.github_agent import GitHubAgent
from models.schemas import Task, AgentType, TaskStatus, RoutingDecision
from utils.logger import Logger, print_summary_panel, print_help
from rich.console import Console

console = Console()
log = Logger("Orchestrator")


class Orchestrator:
    """
    Master controller. Owns the agents and memory.

    Lifecycle:
      orchestrator = Orchestrator()
      orchestrator.handle("https://youtube.com/watch?v=abc123")
      orchestrator.handle("push summary_abc123.md")
      orchestrator.shutdown()
    """

    def __init__(self):
        log.info("Initializing Orchestrator...")

        # Memory — shared across orchestrator and all agents
        self.memory = Memory()

        # LLM client — used for routing decisions
        self._client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

        # Agents — each gets access to shared memory
        self._agents = {
            AgentType.YOUTUBE: YouTubeAgent(self.memory),
            AgentType.GITHUB:  GitHubAgent(self.memory),
        }

        # Track last summary for convenient "push last" commands
        self._last_summary: dict | None = None

        log.success("Orchestrator ready")

    # ─────────────────────────────────────────────────────────────────────────
    # PUBLIC — Main entry point
    # ─────────────────────────────────────────────────────────────────────────

    def handle(self, user_input: str) -> str:
        """
        Processes a single user input string.
        Returns a response string to display to the user.
        Called by main.py in a loop.
        """
        user_input = user_input.strip()

        # Log conversation to memory for context
        self.memory.log_message("user", user_input)

        # Handle built-in commands first (no LLM needed for these)
        builtin_response = self._handle_builtins(user_input)
        if builtin_response is not None:
            self.memory.log_message("assistant", builtin_response)
            return builtin_response

        # Use Claude to decide what to do with the input
        log.thinking("Routing input to the correct agent via Claude...")
        decision = self._route_with_llm(user_input)
        log.info(f"Routing decision → agent={decision.agent} action={decision.action} "
                 f"confidence={decision.confidence:.0%}")
        log.info(f"Claude's reasoning: {decision.reasoning}")

        if decision.agent == AgentType.UNKNOWN:
            response = ("I'm not sure how to handle that. Try:\n"
                        "  • A YouTube URL to summarize\n"
                        "  • 'push <filename>' to push to GitHub\n"
                        "  • 'fetch <filename>' to retrieve from GitHub\n"
                        "  • 'list' to see saved summaries\n"
                        "  • 'help' for all commands")
            self.memory.log_message("assistant", response)
            return response

        # Create task in memory
        task_id = self.memory.create_task(
            agent=decision.agent,
            action=decision.action,
            payload=decision.payload
        )

        task = Task(
            task_id=task_id,
            agent=decision.agent,
            action=decision.action,
            payload=decision.payload
        )

        # Dispatch to the correct agent
        agent = self._agents[decision.agent]
        completed_task = agent.run(task)

        # Handle result
        response = self._format_response(completed_task)
        self.memory.log_message("assistant", response)
        return response

    # ─────────────────────────────────────────────────────────────────────────
    # ROUTING — Claude decides which agent to call
    # ─────────────────────────────────────────────────────────────────────────

    def _route_with_llm(self, user_input: str) -> RoutingDecision:
        """
        Sends user input to Claude and asks it to return a routing decision.

        Claude returns JSON specifying:
          - Which agent to use (youtube / github / unknown)
          - What action to perform
          - What payload to extract from the user's message
        """
        routing_system_prompt = """You are a router for an AI agent system. Given a user message, decide which agent to call.

Available agents and actions:
  youtube → summarize
    payload: {"url": "<youtube_url>"}
    Use when: user provides a YouTube URL or asks to summarize a video

  github → push
    payload: {"filename": "<name>.md", "content": "<markdown>", "commit_message": "<msg>"}
    Use when: user asks to push, save, upload, or commit something to GitHub
    Note: if filename not specified use "summary_latest.md"

  github → fetch
    payload: {"filename": "<filename>"}
    Use when: user asks to fetch, retrieve, get, or download a file from GitHub

  github → list
    payload: {}
    Use when: user asks to list, show, or see files in GitHub repo

  unknown → none
    payload: {}
    Use when: input doesn't match any agent

Return ONLY valid JSON. No markdown fences, no explanation, no extra text. Start your response with { and end with }.
Example:
{"agent": "youtube", "action": "summarize", "payload": {"url": "https://youtube.com/watch?v=abc"}, "confidence": 0.95, "reasoning": "User provided a YouTube URL"}"""

        # Only pass the current user message — no history — to keep routing clean
        response = self._client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system=routing_system_prompt,
            messages=[{"role": "user", "content": user_input}]
        )

        raw = response.content[0].text.strip()
        log.info(f"Raw routing response: {raw[:200]}")

        # Strip markdown code fences if Claude wrapped the JSON
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        try:
            parsed = json.loads(raw)
            return RoutingDecision(**parsed)
        except Exception as e:
            log.error(f"Routing failed to parse Claude's response: {e}\nRaw: '{raw[:300]}'")
            return RoutingDecision(
                agent=AgentType.UNKNOWN,
                action="none",
                payload={},
                confidence=0.0,
                reasoning="Failed to parse routing response"
            )

    # ─────────────────────────────────────────────────────────────────────────
    # BUILT-IN COMMANDS — Handled without LLM
    # ─────────────────────────────────────────────────────────────────────────

    def _handle_builtins(self, user_input: str) -> str | None:
        """
        Handles simple commands that don't need LLM routing.
        Returns a response string, or None if not a built-in command.
        """
        lower = user_input.lower().strip()

        if lower in ("help", "?"):
            print_help()
            return "Help displayed above."

        if lower in ("list", "ls", "show summaries"):
            summaries = self.memory.list_summaries()
            if not summaries:
                return "No summaries saved yet. Paste a YouTube URL to get started."
            lines = ["Saved summaries:"]
            for s in summaries:
                github_indicator = " [GitHub ✓]" if s.get("github_url") else ""
                lines.append(f"  • {s['video_id']} — {s['created_at'][:10]}{github_indicator}")
                lines.append(f"    {s['video_url']}")
            return "\n".join(lines)

        if lower in ("history",):
            tasks = self.memory.get_task_history(limit=10)
            if not tasks:
                return "No task history yet."
            lines = ["Recent tasks:"]
            for t in tasks:
                lines.append(
                    f"  [{t['status'].upper():^10}] {t['agent']:8} | {t['action']:12} | {t['created_at'][:19]}"
                )
            return "\n".join(lines)

        return None   # Not a built-in → let LLM router handle it

    # ─────────────────────────────────────────────────────────────────────────
    # RESPONSE FORMATTING
    # ─────────────────────────────────────────────────────────────────────────

    def _format_response(self, task: Task) -> str:
        """Formats the completed task result into a human-readable response."""

        if task.status == TaskStatus.FAILED:
            return f"Task failed: {task.error}"

        result = task.result or {}

        # YouTube Agent result — rich summary display
        if task.agent == AgentType.YOUTUBE:
            self._last_summary = result
            print_summary_panel(result)

            # After displaying, ask user if they want to push to GitHub
            filename = f"summary_{result.get('video_id', 'unknown')}.md"
            from agents.github_agent import GitHubAgent
            markdown = GitHubAgent.format_summary_as_markdown(result)

            console.print(
                f"\n[bold yellow]Push this summary to GitHub?[/bold yellow] "
                f"It will be saved as '[cyan]{filename}[/cyan]'\n"
                f"Type: [green]push {filename}[/green]   (or just paste another URL)"
            )

            # Store ready-to-push data so GitHub agent can use it
            self.memory.log_message(
                "assistant",
                json.dumps({"pending_push": {"filename": filename, "content": markdown}})
            )
            return f"Summary complete for: {result.get('title', 'video')}"

        # GitHub Agent result — push or fetch
        if task.agent == AgentType.GITHUB:
            action = result.get("action", "")
            if action == "pushed":
                url = result.get("url", "")
                # Update memory with GitHub URL
                video_id = task.payload.get("filename", "").replace("summary_", "").replace(".md", "")
                if video_id:
                    self.memory.update_summary_github_url(video_id, url)
                return f"Pushed to GitHub: {url}"

            if action == "fetched":
                content = result.get("content", "")
                console.print(f"\n[bold cyan]File content:[/bold cyan]\n{content[:2000]}")
                if len(content) > 2000:
                    console.print("[dim]... (truncated, full file on GitHub)[/dim]")
                return f"Fetched from: {result.get('url', '')}"

            if "files" in result:
                files = result["files"]
                if not files:
                    return "Repository is empty."
                lines = [f"Files in GitHub repo ({result['count']} total):"]
                for f in files:
                    lines.append(f"  • {f['name']} — {f['url']}")
                return "\n".join(lines)

        return json.dumps(result, indent=2)

    # ─────────────────────────────────────────────────────────────────────────
    # SHUTDOWN
    # ─────────────────────────────────────────────────────────────────────────

    def shutdown(self):
        """Clean shutdown — close DB connection."""
        self.memory.close()
        log.info("Orchestrator shut down cleanly")
