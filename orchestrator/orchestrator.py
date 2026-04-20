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
import re
import json
import uuid
from anthropic import Anthropic

from orchestrator.memory import Memory
from agents.youtube_agent import YouTubeAgent
from agents.github_agent import GitHubAgent
from agents.jira_agent import JiraAgent
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
            AgentType.JIRA:    JiraAgent(self.memory),
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

IMPORTANT RULE: Any request to "create" a JIRA ticket ALWAYS maps to jira → full_flow.
The full_flow action creates the ticket AND runs the full code pipeline automatically.
Never use jira → create for new ticket requests — it does not exist as a routing option.

Available agents and actions:

  youtube → summarize
    payload: {"url": "<youtube_url>"}
    Use when: user provides a YouTube URL or asks to summarize a video

  github → push
    payload: {"filename": "<name>.md", "content": "", "commit_message": "<msg>"}
    Use when: user asks to push, save, or commit a file to GitHub

  github → fetch
    payload: {"filename": "<filename>"}
    Use when: user asks to fetch or retrieve a file from GitHub

  github → list
    payload: {}
    Use when: user asks to list files in GitHub repo

  jira → full_flow
    payload: {"summary": "...", "issue_type": "Story", "priority": "Medium", "description": "..."}
    Use when: user says ANYTHING like "create jira", "new ticket", "create ticket", "make a ticket",
              "full flow", "do everything", "new story", "raise a ticket", "log a ticket".
    This creates the ticket AND automatically: generates code → creates branch → pushes → code reviews → creates PR → updates JIRA.
    If the user provided a title/summary in their message, extract it into payload.summary.
    If the user did NOT provide a title, leave payload as {} and the system will ask them.

  jira → fetch
    payload: {"ticket_key": "<e.g. SCRUM-42>"}
    Use when: user asks to fetch, view, check, or get details of an EXISTING JIRA ticket by key

  jira → update_status
    payload: {"ticket_key": "<key>", "status": "<status>"}
    Use when: user asks to move, transition, or update the status of an existing JIRA ticket

  jira → add_comment
    payload: {"ticket_key": "<key>", "comment": "<text>"}
    Use when: user asks to add a comment to an existing JIRA ticket

  github → write_code
    payload: {"ticket_key": "<e.g. SCRUM-42>"}
    Use when: user asks to write or generate code for an ALREADY EXISTING ticket by key

  unknown → none
    payload: {}
    Use when: input doesn't match any of the above

Return ONLY valid JSON. No markdown fences. Start with { and end with }.
Example: {"agent": "jira", "action": "fetch", "payload": {"ticket_key": "SCRUM-1"}, "confidence": 0.95, "reasoning": "User asked to view SCRUM-1"}"""

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
    # WEB — Returns structured dict instead of terminal-formatted string
    # ─────────────────────────────────────────────────────────────────────────

    def handle_web(self, user_input: str) -> dict:
        """
        Web version of handle().
        Same routing logic but returns a structured dict for the browser
        instead of Rich terminal output.

        Return shape:
          {"type": "summary|github|jira|text|error|list", "message": "...", "data": {...}}
        """
        user_input = user_input.strip()
        self.memory.log_message("user", user_input)

        # ── Check if we're in mid-full-flow Q&A ─────────────────────────────
        pending_flow = self.memory.get_state("pending_full_flow")
        if pending_flow and pending_flow.get("step") == "awaiting_summary":
            return self._resume_full_autonomous_flow(user_input)

        # ── Check if we're in mid-JIRA creation Q&A ──────────────────────────
        pending = self.memory.get_state("pending_jira")
        if pending and pending.get("step") == "awaiting_answers":
            return self._complete_jira_creation(user_input)

        # Built-in commands
        lower = user_input.lower().strip()

        if lower in ("help", "?"):
            return {
                "type": "help",
                "message": "Available commands",
                "data": {
                    "commands": [
                        {"cmd": "<YouTube URL>",       "desc": "Fetch transcript and generate technical summary"},
                        {"cmd": "push <filename>",     "desc": "Push the last summary to GitHub"},
                        {"cmd": "fetch <filename>",    "desc": "Fetch a file from your GitHub repo"},
                        {"cmd": "list",                "desc": "List all summaries saved in memory"},
                        {"cmd": "history",             "desc": "Show task history"},
                    ]
                }
            }

        if lower in ("list", "ls", "show summaries"):
            summaries = self.memory.list_summaries()
            return {
                "type": "list",
                "message": f"{len(summaries)} summaries found",
                "data": {"summaries": summaries}
            }

        if lower == "history":
            tasks = self.memory.get_task_history(limit=10)
            return {
                "type": "history",
                "message": f"{len(tasks)} recent tasks",
                "data": {"tasks": tasks}
            }

        # LLM routing
        decision = self._route_with_llm(user_input)

        if decision.agent == AgentType.UNKNOWN:
            return {
                "type": "text",
                "message": "I'm not sure how to handle that. Try a YouTube URL, or type 'help'.",
                "data": {}
            }

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

        # ── Special multi-step: write code for a JIRA ticket ─────────────────
        if decision.agent == AgentType.GITHUB and decision.action == "write_code":
            ticket_key = decision.payload.get("ticket_key", "").upper()
            if not ticket_key:
                return {"type": "error", "message": "Please specify a ticket key, e.g. SCRUM-1", "data": {}}
            return self._handle_code_flow(ticket_key)

        # ── Full autonomous flow: create ticket + full pipeline ───────────────
        if decision.agent == AgentType.JIRA and decision.action == "full_flow":
            return self._handle_full_autonomous_flow(user_input, decision.payload)

        agent = self._agents[decision.agent]
        completed_task = agent.run(task)

        if completed_task.status.value == "failed":
            return {"type": "error", "message": completed_task.error, "data": {}}

        result = completed_task.result or {}

        # YouTube summary result
        if completed_task.agent == AgentType.YOUTUBE:
            self._last_summary = result
            filename = f"summary_{result.get('video_id', 'unknown')}.md"
            from agents.github_agent import GitHubAgent
            markdown = GitHubAgent.format_summary_as_markdown(result)
            self.memory.log_message("assistant", f"Summary generated: {result.get('title')}")
            return {
                "type": "summary",
                "message": f"Summary generated for: {result.get('title', 'video')}",
                "data": result,
                "push_hint": {
                    "filename": filename,
                    "content": markdown,
                    "commit_message": f"Add summary: {result.get('title', filename)}"
                }
            }

        # GitHub result
        if completed_task.agent == AgentType.GITHUB:
            action = result.get("action", "")
            if action == "pushed":
                url = result.get("url", "")
                video_id = task.payload.get("filename", "").replace("summary_", "").replace(".md", "")
                if video_id:
                    self.memory.update_summary_github_url(video_id, url)
            self.memory.log_message("assistant", result.get("message", ""))
            return {
                "type": "github",
                "message": result.get("message", ""),
                "data": result
            }

        # JIRA result
        if completed_task.agent == AgentType.JIRA:
            # ask_create returns questions for the web UI to display
            if result.get("type") == "jira_questions":
                return {
                    "type": "jira_questions",
                    "message": "Please answer these questions to create your JIRA ticket:",
                    "data": result
                }
            self.memory.log_message("assistant", result.get("message", str(result)))
            return {
                "type": "jira",
                "message": result.get("message", "JIRA action complete"),
                "data": result
            }

        return {"type": "text", "message": str(result), "data": result}

    # ─────────────────────────────────────────────────────────────────────────
    # JIRA CREATION — Complete ticket after user answers questions
    # ─────────────────────────────────────────────────────────────────────────

    def _complete_jira_creation(self, user_input: str) -> dict:
        """
        Called when pending_jira state exists and user has answered questions.
        Uses Claude to extract structured ticket fields from the user's answer.
        """
        log.thinking("Extracting JIRA ticket details from your answer...")

        system = """You extract JIRA ticket details from user input.
Return ONLY valid JSON with this structure:
{
  "summary": "ticket title",
  "issue_type": "Story",
  "priority": "Medium",
  "description": "full description"
}
Rules:
- issue_type must be one of: Story, Bug, Task, Epic
- priority must be one of: Highest, High, Medium, Low
- If not mentioned, use defaults: issue_type=Story, priority=Medium
- description can be empty string if not provided"""

        response = self._client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            system=system,
            messages=[{"role": "user", "content": user_input}]
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"): raw = raw[4:]
            raw = raw.strip()

        try:
            ticket_data = json.loads(raw)
        except Exception:
            return {"type": "error", "message": "Could not parse your answers. Please try again.", "data": {}}

        # Now create the ticket
        jira_agent = self._agents[AgentType.JIRA]
        task_id = self.memory.create_task("jira", "create", ticket_data)
        task = Task(task_id=task_id, agent=AgentType.JIRA,
                    action="create", payload=ticket_data)
        completed = jira_agent.run(task)

        if completed.status.value == "failed":
            return {"type": "error", "message": completed.error, "data": {}}

        result = completed.result or {}
        return {
            "type": "jira",
            "message": f"Ticket created: {result.get('key')} — {result.get('summary')}",
            "data": result
        }

    # ─────────────────────────────────────────────────────────────────────────
    # CODE FLOW — Full pipeline: fetch ticket → generate code → PR → review → update JIRA
    # ─────────────────────────────────────────────────────────────────────────

    def _handle_code_flow(self, ticket_key: str) -> dict:
        """
        Orchestrates the full code-writing pipeline:
          1. Fetch JIRA ticket details
          2. Generate code using Claude
          3. Create a feature branch
          4. Push code to branch
          5. Create Pull Request
          6. Code review via Claude
          7. Update JIRA ticket with PR URL + review
        Returns a structured result for the web UI.
        """
        log.info(f"Starting code flow for {ticket_key}")
        steps = []

        jira_agent   = self._agents[AgentType.JIRA]
        github_agent = self._agents[AgentType.GITHUB]

        # ── Step 1: Fetch JIRA Ticket ─────────────────────────────────────────
        log.thinking(f"Fetching {ticket_key} from JIRA...")
        t1_id = self.memory.create_task("jira", "fetch", {"ticket_key": ticket_key})
        t1    = Task(task_id=t1_id, agent=AgentType.JIRA,
                     action="fetch", payload={"ticket_key": ticket_key})
        t1    = jira_agent.run(t1)

        if t1.status.value == "failed":
            return {"type": "error", "message": f"Could not fetch {ticket_key}: {t1.error}", "data": {}}

        ticket        = t1.result
        ticket_summary = ticket.get("summary", "")
        ticket_desc   = ticket.get("description", ticket_summary)
        steps.append({"step": "Fetched JIRA ticket", "detail": ticket_summary})
        log.success(f"Ticket fetched: {ticket_summary}")

        # ── Step 2: Generate Code with Claude ─────────────────────────────────
        log.thinking("Generating code based on ticket description...")
        code, filename = self._generate_code(ticket_key, ticket_summary, ticket_desc)
        steps.append({"step": "Code generated", "detail": filename})

        # ── Step 3: Create Feature Branch ─────────────────────────────────────
        slug        = re.sub(r"[^a-z0-9]+", "-", ticket_summary.lower())[:40]
        branch_name = f"feature/{ticket_key.lower()}-{slug}"

        t3_id = self.memory.create_task("github", "create_branch", {"branch_name": branch_name})
        t3    = Task(task_id=t3_id, agent=AgentType.GITHUB,
                     action="create_branch", payload={"branch_name": branch_name})
        t3    = github_agent.run(t3)

        if t3.status.value == "failed":
            return {"type": "error", "message": f"Branch creation failed: {t3.error}", "data": {}}
        steps.append({"step": "Branch created", "detail": branch_name})

        # ── Step 4: Push Code ─────────────────────────────────────────────────
        push_payload = {
            "branch_name":    branch_name,
            "filepath":       f"code/{filename}",
            "content":        code,
            "commit_message": f"{ticket_key}: {ticket_summary}"
        }
        t4_id = self.memory.create_task("github", "push_code", push_payload)
        t4    = Task(task_id=t4_id, agent=AgentType.GITHUB,
                     action="push_code", payload=push_payload)
        t4    = github_agent.run(t4)

        if t4.status.value == "failed":
            return {"type": "error", "message": f"Code push failed: {t4.error}", "data": {}}
        steps.append({"step": "Code pushed", "detail": t4.result.get("url", "")})

        # ── Step 5: Code Review ───────────────────────────────────────────────
        log.thinking("Performing code review...")
        review_payload = {
            "code":           code,
            "filename":       filename,
            "ticket_summary": ticket_summary
        }
        t5_id = self.memory.create_task("github", "code_review", review_payload)
        t5    = Task(task_id=t5_id, agent=AgentType.GITHUB,
                     action="code_review", payload=review_payload)
        t5    = github_agent.run(t5)

        review         = t5.result or {}
        review_score   = review.get("score", 0)
        review_comment = review.get("jira_comment", "Code review complete")
        steps.append({"step": "Code review complete",
                       "detail": f"Score: {review_score}/10"})

        # ── Step 6: Create PR (only if review score ≥ 6) ─────────────────────
        pr_url    = ""
        pr_number = 0

        if review_score >= 6:
            pr_payload = {
                "branch_name": branch_name,
                "title":       f"[{ticket_key}] {ticket_summary}",
                "body":        (f"Resolves {ticket_key}\n\n{ticket_desc}\n\n"
                                f"---\n*Code Review Score: {review_score}/10*\n\n"
                                f"{review_comment}\n\n"
                                f"_Auto-generated by AI Orchestrator._")
            }
            t6_id = self.memory.create_task("github", "create_pr", pr_payload)
            t6    = Task(task_id=t6_id, agent=AgentType.GITHUB,
                         action="create_pr", payload=pr_payload)
            t6    = github_agent.run(t6)

            if t6.status.value == "failed":
                steps.append({"step": "PR creation failed", "detail": t6.error})
            else:
                pr_url    = t6.result.get("pr_url", "")
                pr_number = t6.result.get("pr_number", 0)
                steps.append({"step": "Pull Request created", "detail": pr_url})
        else:
            steps.append({
                "step":   "PR skipped — review score too low",
                "detail": f"Score {review_score}/10 is below threshold (6). Fix issues and retry."
            })
            log.warning(f"Review score {review_score}/10 — skipping PR creation")

        # ── Step 7: Update JIRA status + add PR URL + review ─────────────────
        if pr_url:
            update_payload = {
                "ticket_key":     ticket_key,
                "pr_url":         pr_url,
                "review_summary": review_comment
            }
            t7_id = self.memory.create_task("jira", "update_pr", update_payload)
            t7    = Task(task_id=t7_id, agent=AgentType.JIRA,
                         action="update_pr", payload=update_payload)
            jira_agent.run(t7)

            # Move ticket to "In Review"
            ts_id = self.memory.create_task("jira", "update_status",
                                            {"ticket_key": ticket_key, "status": "In Review"})
            ts    = Task(task_id=ts_id, agent=AgentType.JIRA, action="update_status",
                         payload={"ticket_key": ticket_key, "status": "In Review"})
            jira_agent.run(ts)
            steps.append({"step": "JIRA updated → In Review", "detail": f"PR linked: {pr_url}"})
        else:
            # Still post the review as a comment even if no PR
            comment_text = f"Code review complete (score {review_score}/10) — PR not created.\n\n{review_comment}"
            c_id = self.memory.create_task("jira", "add_comment",
                                           {"ticket_key": ticket_key, "comment": comment_text})
            ct   = Task(task_id=c_id, agent=AgentType.JIRA, action="add_comment",
                        payload={"ticket_key": ticket_key, "comment": comment_text})
            jira_agent.run(ct)
            steps.append({"step": "Code review posted to JIRA", "detail": ticket_key})

        log.success(f"Code flow complete for {ticket_key}")

        return {
            "type":    "code_flow",
            "message": f"Code pipeline complete for {ticket_key}",
            "data": {
                "ticket_key":     ticket_key,
                "ticket_summary": ticket_summary,
                "ticket_url":     ticket.get("url", ""),
                "branch_name":    branch_name,
                "filename":       f"code/{filename}",
                "pr_url":         pr_url,
                "pr_number":      pr_number,
                "review":         review,
                "steps":          steps,
                "code_preview":   code[:800]
            }
        }

    def _generate_code(self, ticket_key: str, summary: str, description: str) -> tuple[str, str]:
        """
        Uses Claude Sonnet to generate code based on the JIRA ticket.
        Returns (code_string, filename).
        """
        system = """You are a senior software engineer. Generate clean, production-ready Python code.
Return a JSON object with exactly two keys:
{
  "filename": "descriptive_name.py",
  "code": "full python code here"
}
Rules:
- filename: snake_case, descriptive, ends in .py
- code: complete, runnable Python with docstrings and type hints
- Include error handling where appropriate
- Return ONLY valid JSON, no markdown fences"""

        prompt = f"""Write Python code for this JIRA ticket:

Ticket: {ticket_key}
Summary: {summary}
Description: {description}"""

        response = self._client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=3000,
            system=system,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"): raw = raw[4:]
            raw = raw.strip()

        try:
            result   = json.loads(raw)
            return result["code"], result["filename"]
        except Exception:
            # Fallback: treat entire response as code
            return raw, f"{ticket_key.lower().replace('-', '_')}_impl.py"

    # ─────────────────────────────────────────────────────────────────────────
    # FULL AUTONOMOUS FLOW — Create ticket + full pipeline in one shot
    # ─────────────────────────────────────────────────────────────────────────

    def _handle_full_autonomous_flow(self, user_input: str, routed_payload: dict) -> dict:
        """
        Entry point for the fully autonomous pipeline.

        If the user already provided enough detail (summary extracted by router),
        go straight to ticket creation → pipeline.

        If not, ask ONE question and save state so _resume_full_autonomous_flow()
        picks it up on the next message.
        """
        summary = routed_payload.get("summary", "").strip()

        # If router didn't extract a summary, ask the user for ONE thing
        if not summary:
            self.memory.set_state("pending_full_flow", {"step": "awaiting_summary"})
            return {
                "type":    "text",
                "message": ("Sure! To kick off the full pipeline, I just need one thing:\n\n"
                            "**What should the ticket be about?**\n"
                            "Describe the feature or bug in 1–2 sentences "
                            "(e.g. *\"Implement JWT authentication for the login endpoint\"*)."),
                "data":    {}
            }

        # We have enough info — build ticket payload and run
        ticket_payload = {
            "summary":    summary,
            "issue_type": routed_payload.get("issue_type", "Story"),
            "priority":   routed_payload.get("priority", "Medium"),
            "description": routed_payload.get("description", summary),
        }
        return self._create_ticket_then_pipeline(ticket_payload)

    def _resume_full_autonomous_flow(self, user_input: str) -> dict:
        """
        Called when pending_full_flow state exists.
        Extracts ticket info from user's answer and runs the pipeline.
        """
        self.memory.clear_state("pending_full_flow")
        log.thinking("Extracting ticket details from your answer...")

        system = """Extract a JIRA ticket description from user input.
Return ONLY valid JSON:
{
  "summary": "short one-line ticket title",
  "issue_type": "Story",
  "priority": "Medium",
  "description": "detailed description"
}
Rules:
- issue_type must be one of: Story, Bug, Task, Epic
- priority must be one of: Highest, High, Medium, Low
- If not mentioned, default issue_type=Story, priority=Medium
- summary must be concise (under 100 chars)"""

        response = self._client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            system=system,
            messages=[{"role": "user", "content": user_input}]
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        try:
            ticket_payload = json.loads(raw)
        except Exception:
            return {"type": "error", "message": "Could not parse your description. Please try again.", "data": {}}

        return self._create_ticket_then_pipeline(ticket_payload)

    def _create_ticket_then_pipeline(self, ticket_payload: dict) -> dict:
        """
        Creates a JIRA ticket, then immediately kicks off the full code pipeline.
        Returns the combined result.
        """
        jira_agent = self._agents[AgentType.JIRA]

        # ── Create JIRA ticket ────────────────────────────────────────────────
        log.thinking(f"Creating JIRA ticket: {ticket_payload.get('summary')}")
        t_id = self.memory.create_task("jira", "create", ticket_payload)
        t    = Task(task_id=t_id, agent=AgentType.JIRA,
                    action="create", payload=ticket_payload)
        t    = jira_agent.run(t)

        if t.status.value == "failed":
            return {"type": "error", "message": f"Failed to create ticket: {t.error}", "data": {}}

        ticket_key = t.result.get("key", "")
        log.success(f"Ticket created: {ticket_key}")

        # ── Run the full code pipeline ────────────────────────────────────────
        pipeline_result = self._handle_code_flow(ticket_key)

        # Inject ticket creation info at the top of the steps list
        if pipeline_result.get("type") == "code_flow":
            pipeline_result["data"]["steps"].insert(0, {
                "step":   "JIRA ticket created",
                "detail": f"{ticket_key} — {ticket_payload.get('summary')}"
            })
            pipeline_result["message"] = (
                f"Full pipeline complete: {ticket_key} created, coded, reviewed, and PR raised"
            )
            pipeline_result["data"]["ticket_created"] = True

        return pipeline_result

    # ─────────────────────────────────────────────────────────────────────────
    # SHUTDOWN
    # ─────────────────────────────────────────────────────────────────────────

    def shutdown(self):
        """Clean shutdown — close DB connection."""
        self.memory.close()
        log.info("Orchestrator shut down cleanly")
