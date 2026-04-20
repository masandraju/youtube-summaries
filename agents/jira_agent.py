"""
agents/jira_agent.py
─────────────────────
JIRA Agent — creates, fetches, and updates JIRA tickets.

ACTIONS:
  ask_create   → Returns questions to ask user before creating ticket
  create       → Creates a ticket from collected answers
  fetch        → Gets ticket details by key (e.g., SCRUM-42)
  update_status → Transitions ticket to new status
  add_comment  → Posts a comment (used for PR URL + code review)
  update_pr    → Links PR URL to ticket as remote link + comment

AUTHENTICATION:
  Uses Atlassian API Token (Basic auth: email:token base64 encoded)
  Stored in .env — never hardcoded
"""

import os
import json
import requests
from atlassian import Jira
from anthropic import Anthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from agents.base_agent import BaseAgent
from models.schemas import Task, JiraTicketInput, JiraTicket, JiraUpdateInput
from orchestrator.memory import Memory


class JiraAgent(BaseAgent):
    """
    Handles all JIRA operations:
      - Multi-step ticket creation (asks questions, then creates)
      - Fetching ticket details
      - Updating status through workflow transitions
      - Adding comments (PR URLs, code review summaries)
    """

    ISSUE_TYPES = ["Story", "Bug", "Task", "Epic"]
    PRIORITIES  = ["Highest", "High", "Medium", "Low"]

    def __init__(self, memory: Memory):
        super().__init__(memory)
        # Strip whitespace — .env values sometimes have trailing spaces/tabs
        self._base_url    = os.getenv("JIRA_BASE_URL",    "").strip().rstrip("/")
        self._email       = os.getenv("JIRA_EMAIL",       "").strip()
        self._token       = os.getenv("JIRA_API_TOKEN",   "").strip()
        self._project_key = os.getenv("JIRA_PROJECT_KEY", "SCRUM").strip()

        self._jira = Jira(
            url=self._base_url,
            username=self._email,
            password=self._token,
            cloud=True
        )
        self._auth    = (self._email, self._token)
        self._headers = {"Accept": "application/json", "Content-Type": "application/json"}
        self._llm     = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    @property
    def name(self) -> str:
        return "JIRA Agent"

    # ─────────────────────────────────────────────────────────────────────────
    # MAIN ENTRY POINT
    # ─────────────────────────────────────────────────────────────────────────

    def _execute(self, task: Task) -> dict:
        action = task.action.lower()
        if action == "ask_create":   return self._ask_create_questions()
        if action == "create":       return self._create_ticket(task.payload)
        if action == "fetch":        return self._fetch_ticket(task.payload)
        if action == "update_status":return self._update_status(task.payload)
        if action == "add_comment":  return self._add_comment(task.payload)
        if action == "update_pr":    return self._update_pr(task.payload)
        raise ValueError(f"Unknown JIRA action: '{action}'")

    # ─────────────────────────────────────────────────────────────────────────
    # ASK QUESTIONS — Returns what to ask the user before creating a ticket
    # ─────────────────────────────────────────────────────────────────────────

    def _ask_create_questions(self) -> dict:
        """
        Returns a structured questions payload.
        The web UI renders this as a form the user fills in.
        The orchestrator stores this as 'pending_jira' state in memory.
        """
        self.log.info("Preparing ticket creation questions")
        questions = {
            "summary":     {"label": "Ticket Title / Summary",       "required": True,  "placeholder": "e.g. Implement user login with JWT"},
            "issue_type":  {"label": "Issue Type",                    "required": True,  "options": self.ISSUE_TYPES, "default": "Story"},
            "priority":    {"label": "Priority",                      "required": True,  "options": self.PRIORITIES,  "default": "Medium"},
            "description": {"label": "Description (what needs to be done)", "required": False, "placeholder": "Detailed description of the work..."},
        }
        # Save state so orchestrator knows we're mid-creation
        self.memory.set_state("pending_jira", {"questions": questions, "step": "awaiting_answers"})
        return {"type": "jira_questions", "questions": questions}

    # ─────────────────────────────────────────────────────────────────────────
    # CREATE TICKET
    # ─────────────────────────────────────────────────────────────────────────

    # ─────────────────────────────────────────────────────────────────────────
    # AUTH CHECK — called before any write operation
    # ─────────────────────────────────────────────────────────────────────────

    def _check_auth(self):
        """
        Calls /rest/api/2/myself to verify credentials before doing writes.
        Raises a clear RuntimeError if authentication fails.
        """
        resp = requests.get(
            f"{self._base_url}/rest/api/2/myself",
            auth=self._auth,
            headers=self._headers,
            timeout=10
        )
        if resp.status_code == 401:
            raise RuntimeError(
                "JIRA authentication failed (401). "
                "Your API token may be expired or the email doesn't match. "
                "Go to https://id.atlassian.com/manage-profile/security/api-tokens, "
                "create a new token, and update JIRA_API_TOKEN in your .env file."
            )
        if not resp.ok:
            raise RuntimeError(f"JIRA auth check failed ({resp.status_code}): {resp.text[:200]}")

        account = resp.json().get("displayName", "unknown")
        self.log.info(f"JIRA auth OK — logged in as '{account}'")

    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS — shared HTTP call with clear error surfacing
    # ─────────────────────────────────────────────────────────────────────────

    def _post(self, path: str, body: dict) -> dict:
        """POST to a JIRA REST v2 endpoint, surfacing the real error on failure."""
        resp = requests.post(
            f"{self._base_url}{path}",
            json=body,
            auth=self._auth,
            headers=self._headers,
            timeout=15
        )
        if not resp.ok:
            try:
                err  = resp.json()
                msgs = err.get("errorMessages", [])
                errs = err.get("errors", {})
                detail = "; ".join(msgs) if msgs else (json.dumps(errs) if errs else resp.text[:300])
            except Exception:
                detail = resp.text[:300]
            raise RuntimeError(f"JIRA {resp.status_code}: {detail}")
        return resp.json() if resp.text else {}

    def _create_ticket(self, payload: dict) -> dict:
        """
        Creates a JIRA ticket via REST v2.
        payload: {summary, issue_type, priority, description}
        """
        inp = JiraTicketInput(**payload)
        self.log.info(f"Creating JIRA ticket: '{inp.summary}' [{inp.issue_type} / {inp.priority}]")

        # Verify credentials before attempting — gives a clear error if token is wrong
        self._check_auth()

        fields = {
            "project":   {"key": self._project_key},
            "summary":   inp.summary,
            "issuetype": {"name": inp.issue_type},
            "priority":  {"name": inp.priority},
        }
        if inp.description:
            fields["description"] = inp.description

        data       = self._post("/rest/api/2/issue", {"fields": fields})
        ticket_key = data.get("key")
        if not ticket_key:
            raise RuntimeError(f"JIRA returned no key: {data}")

        ticket_url = f"{self._base_url}/browse/{ticket_key}"
        self.log.success(f"Ticket created → {ticket_key} — {ticket_url}")

        self.memory.clear_state("pending_jira")
        self.memory.clear_state("pending_full_flow")

        result = {
            "key":         ticket_key,
            "summary":     inp.summary,
            "issue_type":  inp.issue_type,
            "priority":    inp.priority,
            "status":      "To Do",
            "description": inp.description,
            "url":         ticket_url,
        }
        self.memory.cache_jira_ticket(result)
        return result

    # ─────────────────────────────────────────────────────────────────────────
    # FETCH TICKET
    # ─────────────────────────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=5))
    def _fetch_ticket(self, payload: dict) -> dict:
        """
        Fetches full ticket details from JIRA.
        payload: {ticket_key: "SCRUM-42"}
        """
        ticket_key = payload.get("ticket_key", "").upper().strip()
        if not ticket_key:
            raise ValueError("ticket_key is required to fetch a JIRA ticket")

        self.log.info(f"Fetching ticket {ticket_key}...")
        issue = self._jira.get_issue(ticket_key)

        fields = issue.get("fields", {})

        # Extract description text from ADF or plain text
        desc = fields.get("description", "")
        if isinstance(desc, dict):
            desc = self._extract_text_from_adf(desc)
        desc = desc or ""

        # Extract assignee
        assignee_field = fields.get("assignee") or {}
        assignee = assignee_field.get("displayName", "Unassigned")

        result = {
            "key":        ticket_key,
            "summary":    fields.get("summary", ""),
            "status":     fields.get("status", {}).get("name", "Unknown"),
            "issue_type": fields.get("issuetype", {}).get("name", ""),
            "priority":   (fields.get("priority") or {}).get("name", ""),
            "description":desc,
            "assignee":   assignee,
            "url":        f"{self._base_url}/browse/{ticket_key}",
        }

        self.memory.cache_jira_ticket(result)
        self.log.success(f"Ticket fetched → {ticket_key} [{result['status']}]")
        return result

    # ─────────────────────────────────────────────────────────────────────────
    # UPDATE STATUS
    # ─────────────────────────────────────────────────────────────────────────

    def _update_status(self, payload: dict) -> dict:
        """
        Transitions a ticket to a new status.
        payload: {ticket_key: "SCRUM-42", status: "In Progress"}

        JIRA uses named transitions — the agent looks up valid transitions
        and picks the closest match using Claude.
        """
        ticket_key = payload.get("ticket_key", "").upper()
        target_status = payload.get("status", "")

        self.log.info(f"Transitioning {ticket_key} → '{target_status}'")

        # Get available transitions for this ticket
        transitions = self._jira.get_issue_transitions(ticket_key)
        available = [t["name"] for t in transitions.get("transitions", [])]
        self.log.info(f"Available transitions: {available}")

        if not available:
            raise RuntimeError(f"No transitions available for {ticket_key}")

        # Find best match (exact first, then Claude picks)
        match = next((t for t in available if t.lower() == target_status.lower()), None)
        if not match:
            match = self._pick_best_transition(target_status, available)

        self._jira.set_issue_status(ticket_key, match)
        self.log.success(f"{ticket_key} transitioned → '{match}'")

        return {
            "ticket_key":  ticket_key,
            "new_status":  match,
            "url":         f"{self._base_url}/browse/{ticket_key}",
            "message":     f"Ticket {ticket_key} moved to '{match}'"
        }

    def _pick_best_transition(self, target: str, available: list) -> str:
        """Uses Claude to pick the best matching transition name."""
        prompt = (f"The user wants to transition a JIRA ticket to '{target}'. "
                  f"Available transitions: {available}. "
                  f"Return ONLY the exact transition name from the list that best matches. "
                  f"No explanation, just the name.")
        resp = self._llm.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=50,
            messages=[{"role": "user", "content": prompt}]
        )
        chosen = resp.content[0].text.strip()
        # Safety: ensure it's actually in the list
        return chosen if chosen in available else available[0]

    # ─────────────────────────────────────────────────────────────────────────
    # ADD COMMENT
    # ─────────────────────────────────────────────────────────────────────────

    def _add_comment(self, payload: dict) -> dict:
        """
        Adds a comment to a JIRA ticket via REST v2.
        payload: {ticket_key: "SCRUM-42", comment: "..."}
        """
        ticket_key = payload.get("ticket_key", "").upper()
        comment    = payload.get("comment", "")

        if not comment:
            raise ValueError("comment text is required")

        self.log.info(f"Adding comment to {ticket_key}...")
        self._post(f"/rest/api/2/issue/{ticket_key}/comment", {"body": comment})
        self.log.success(f"Comment added to {ticket_key}")

        return {
            "ticket_key": ticket_key,
            "message":    f"Comment added to {ticket_key}",
            "url":        f"{self._base_url}/browse/{ticket_key}"
        }

    # ─────────────────────────────────────────────────────────────────────────
    # UPDATE PR URL — Links PR to ticket via comment
    # ─────────────────────────────────────────────────────────────────────────

    def _update_pr(self, payload: dict) -> dict:
        """
        Links a Pull Request to a JIRA ticket.
        Adds PR URL as a comment and updates the local cache.
        payload: {ticket_key: "SCRUM-42", pr_url: "https://github.com/.../pull/1",
                  review_summary: "..."}
        """
        ticket_key     = payload.get("ticket_key", "").upper()
        pr_url         = payload.get("pr_url", "")
        review_summary = payload.get("review_summary", "")

        comment = f"Pull Request created: {pr_url}"
        if review_summary:
            comment += f"\n\n*Code Review Summary:*\n{review_summary}"

        self._add_comment({"ticket_key": ticket_key, "comment": comment})
        self.memory.update_jira_pr_url(ticket_key, pr_url)

        self.log.success(f"PR URL linked to {ticket_key}")
        return {
            "ticket_key": ticket_key,
            "pr_url":     pr_url,
            "message":    f"PR linked to {ticket_key} and code review posted",
            "url":        f"{self._base_url}/browse/{ticket_key}"
        }

    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_text_from_adf(adf: dict) -> str:
        """
        Extracts plain text from Atlassian Document Format (ADF).
        JIRA Cloud returns descriptions in ADF JSON format.
        """
        texts = []
        def walk(node):
            if isinstance(node, dict):
                if node.get("type") == "text":
                    texts.append(node.get("text", ""))
                for child in node.get("content", []):
                    walk(child)
            elif isinstance(node, list):
                for item in node:
                    walk(item)
        walk(adf)
        return " ".join(texts).strip()
