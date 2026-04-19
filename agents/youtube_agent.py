"""
agents/youtube_agent.py
───────────────────────
YouTube Agent — fetches transcript and generates technical summary via Claude.

FLOW:
  1. Extract video ID from URL
  2. Check memory cache (skip API if already processed)
  3. Fetch transcript via youtube-transcript-api (no API key needed)
  4. Send transcript to Claude with a structured summarization prompt
  5. Parse Claude's response into TechnicalSummary model
  6. Cache result in memory
  7. Return summary to Orchestrator
"""

import re
import json
import os
from youtube_transcript_api import YouTubeTranscriptApi
from anthropic import Anthropic

from agents.base_agent import BaseAgent
from models.schemas import Task, YouTubeInput, TechnicalSummary
from orchestrator.memory import Memory


class YouTubeAgent(BaseAgent):
    """
    Handles everything related to YouTube:
      - URL parsing
      - Transcript fetching
      - LLM-based technical summarization
    """

    def __init__(self, memory: Memory):
        super().__init__(memory)
        self._client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    @property
    def name(self) -> str:
        return "YouTube Agent"

    # ─────────────────────────────────────────────────────────────────────────
    # MAIN ENTRY POINT (called by BaseAgent.run)
    # ─────────────────────────────────────────────────────────────────────────

    def _execute(self, task: Task) -> dict:
        """
        Orchestrator calls this via BaseAgent.run(task).
        task.payload = {"url": "https://youtube.com/watch?v=..."}
        """
        # Validate input using Pydantic
        inp = YouTubeInput(**task.payload)
        url = inp.url

        # Extract video ID from URL
        video_id = self._extract_video_id(url)
        self.log.info(f"Video ID extracted → {video_id}")

        # Check cache first — avoid redundant API calls
        cached = self.memory.get_cached_summary(video_id)
        if cached:
            self.log.success("Returning cached summary (no API calls needed)")
            return cached

        # Fetch transcript
        self.log.info("Fetching transcript from YouTube...")
        transcript_text = self._fetch_transcript(video_id)
        word_count = len(transcript_text.split())
        self.log.success(f"Transcript fetched — {word_count} words")

        # Generate summary via Claude
        self.log.thinking("Sending transcript to Claude for summarization...")
        summary = self._summarize_with_llm(video_id, url, transcript_text, word_count)
        self.log.success(f"Summary generated — '{summary['title']}'")

        # Cache in memory
        self.memory.cache_summary(video_id, url, summary)

        return summary

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 1 — Extract Video ID
    # ─────────────────────────────────────────────────────────────────────────

    def _extract_video_id(self, url: str) -> str:
        """
        Handles all YouTube URL formats:
          - https://www.youtube.com/watch?v=dQw4w9WgXcQ
          - https://youtu.be/dQw4w9WgXcQ
          - https://youtube.com/shorts/dQw4w9WgXcQ
        """
        patterns = [
            r"(?:v=)([a-zA-Z0-9_-]{11})",       # Standard watch URL
            r"youtu\.be/([a-zA-Z0-9_-]{11})",    # Short URL
            r"shorts/([a-zA-Z0-9_-]{11})",       # Shorts URL
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)

        raise ValueError(f"Could not extract video ID from URL: {url}")

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 2 — Fetch Transcript
    # ─────────────────────────────────────────────────────────────────────────

    def _fetch_transcript(self, video_id: str) -> str:
        """
        Fetches transcript using youtube-transcript-api.

        API changed in v0.6.x — now instance-based instead of class-based.
        This method handles both old (<0.6) and new (>=0.6) versions automatically.
        """
        transcript_data = None

        # ── New API (>= 0.6.x): instantiate the class first ──────────────────
        try:
            api = YouTubeTranscriptApi()

            # Try English first
            try:
                transcript_data = api.fetch(video_id, languages=["en"])
                self.log.info("Fetched English transcript (new API)")
            except Exception:
                # Fall back to any available language
                self.log.warning("English not found — trying any available language (new API)")
                transcript_list = api.list(video_id)
                for t in transcript_list:
                    self.log.info(f"Using transcript: lang={t.language_code}")
                    transcript_data = api.fetch(video_id, languages=[t.language_code])
                    break

        except AttributeError:
            # ── Old API (< 0.6.x): class-method style ────────────────────────
            self.log.info("Falling back to old API style (< 0.6)")
            try:
                transcript_data = YouTubeTranscriptApi.get_transcript(video_id, languages=["en"])
            except Exception:
                transcript_list_obj = YouTubeTranscriptApi.list_transcripts(video_id)
                for t in transcript_list_obj:
                    transcript_data = t.fetch()
                    break

        if not transcript_data:
            raise RuntimeError(f"No transcript found for video: {video_id}")

        # Support both dict-style {"text": ...} and object-style .text segments
        texts = []
        for segment in transcript_data:
            if isinstance(segment, dict):
                texts.append(segment.get("text", ""))
            else:
                texts.append(getattr(segment, "text", str(segment)))

        return " ".join(texts)

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 3 — LLM Summarization via Claude
    # ─────────────────────────────────────────────────────────────────────────

    def _summarize_with_llm(self, video_id: str, url: str,
                             transcript: str, word_count: int) -> dict:
        """
        Sends the transcript to Claude with a carefully engineered prompt.

        WHY THIS PROMPT DESIGN?
          - Claude is instructed to return ONLY valid JSON (no markdown fences)
          - The JSON schema is spelled out explicitly to avoid hallucination
          - We limit transcript to 12000 words to stay within context limits
            while preserving the most important content
        """
        # Truncate very long transcripts to avoid exceeding context window
        max_words = 12000
        words = transcript.split()
        if len(words) > max_words:
            self.log.warning(f"Transcript truncated from {len(words)} → {max_words} words")
            transcript = " ".join(words[:max_words])

        system_prompt = """You are a senior software engineer creating technical summaries of YouTube videos.
Your task is to analyze the transcript and return a structured JSON summary.

IMPORTANT: Return ONLY valid JSON. No markdown, no code fences, no explanation.
The JSON must match this exact structure:
{
  "title": "string — inferred video title from content",
  "overview": "string — 2-3 sentence technical overview",
  "key_points": ["string", "string", ...],
  "technical_concepts": ["string", ...],
  "code_snippets": ["string describing code shown/mentioned", ...],
  "tools_mentioned": ["library/tool/framework names", ...],
  "target_audience": "string — e.g. 'Python beginners' or 'Senior backend engineers'",
  "difficulty_level": "Beginner | Intermediate | Advanced"
}

Rules:
- key_points: 5-8 concise bullet points capturing core technical takeaways
- technical_concepts: actual tech terms, algorithms, patterns mentioned
- code_snippets: describe actual code shown or discussed (empty list if none)
- tools_mentioned: specific library/framework/tool names only
- difficulty_level: choose ONE of Beginner, Intermediate, Advanced"""

        user_message = f"Summarize this YouTube video transcript:\n\n{transcript}"

        response = self._client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}]
        )

        raw_json = response.content[0].text.strip()

        # Parse and validate Claude's response
        try:
            parsed = json.loads(raw_json)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Claude returned invalid JSON: {e}\nRaw: {raw_json[:300]}")

        # Add fields that Claude doesn't fill (we have them from context)
        parsed["video_id"] = video_id
        parsed["video_url"] = url
        parsed["transcript_length"] = word_count

        # Validate the full structure via Pydantic
        summary = TechnicalSummary(**parsed)
        return summary.model_dump(mode="json")
