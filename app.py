"""
app.py
──────
Flask web server for the YouTube ↔ GitHub AI Orchestrator.

Run with:
    py app.py

Endpoints:
    GET  /          → serves the chat UI (index.html)
    POST /api/chat  → accepts {message} → returns structured JSON response
    GET  /api/history → returns last 10 tasks from memory
"""

import os
import sys
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv

load_dotenv()

from orchestrator.orchestrator import Orchestrator

app = Flask(__name__)

# ── Initialize Orchestrator once at startup ───────────────────────────────────
try:
    orchestrator = Orchestrator()
except Exception as e:
    print(f"[ERROR] Failed to initialize Orchestrator: {e}")
    sys.exit(1)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Serves the main chat UI."""
    return render_template("index.html")


@app.route("/api/chat", methods=["POST"])
def chat():
    """
    Receives a user message and returns a structured response.

    Request body:  {"message": "https://youtube.com/..."}
    Response body: {"type": "summary", "message": "...", "data": {...}}
    """
    body = request.get_json(silent=True)
    if not body or not body.get("message", "").strip():
        return jsonify({"type": "error", "message": "Empty message received.", "data": {}}), 400

    user_message = body["message"].strip()

    try:
        response = orchestrator.handle_web(user_message)
        return jsonify(response)
    except Exception as e:
        return jsonify({"type": "error", "message": str(e), "data": {}}), 500


@app.route("/api/history", methods=["GET"])
def history():
    """Returns recent task history from memory."""
    tasks = orchestrator.memory.get_task_history(limit=10)
    return jsonify({"tasks": tasks})


@app.route("/api/summaries", methods=["GET"])
def summaries():
    """Returns all cached summaries."""
    items = orchestrator.memory.list_summaries()
    return jsonify({"summaries": items})


# ── Entry Point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n YouTube ↔ GitHub AI Orchestrator (Web)\n")
    print("  Open in browser: http://localhost:5000\n")
    app.run(debug=True, port=5000)
