"""
agents/base_agent.py
────────────────────
Abstract base class for all agents.

WHY A BASE CLASS?
  - Enforces a standard interface: every agent MUST implement `run()`
  - Shared retry logic — one place to fix, all agents benefit
  - Shared access to Memory and Logger
  - Orchestrator can call any agent the same way: agent.run(task)

PATTERN: Template Method
  BaseAgent defines the structure (run → _execute → result)
  Subclasses fill in the _execute() implementation
"""

from abc import ABC, abstractmethod
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from utils.logger import Logger
from orchestrator.memory import Memory
from models.schemas import Task, TaskStatus
from datetime import datetime


class BaseAgent(ABC):
    """
    All agents inherit from this. Provides:
      - Standard run() method with error handling
      - Retry logic via tenacity for transient failures
      - Memory access
      - Logging

    To create a new agent:
        class MyAgent(BaseAgent):
            def _execute(self, task: Task) -> dict:
                # your logic here
                return {"result": "..."}
    """

    def __init__(self, memory: Memory):
        self.memory = memory
        self.log = Logger(self.name)         # Each agent uses its own logger

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable agent name (used in logs)."""
        ...

    @abstractmethod
    def _execute(self, task: Task) -> dict:
        """
        Core agent logic. Implemented by each subclass.

        Args:
            task: The Task object with action + payload

        Returns:
            dict: Result to store in task.result

        Raises:
            Exception: On unrecoverable failure
        """
        ...

    def run(self, task: Task) -> Task:
        """
        Public interface called by the Orchestrator.

        Wraps _execute() with:
          - Status updates in Memory
          - Error catching and logging
          - Timing

        The Orchestrator always calls agent.run(task) — never _execute() directly.
        """
        self.log.info(f"Starting task {task.task_id[:8]}... action='{task.action}'")
        self.memory.update_task_status(task.task_id, TaskStatus.RUNNING)

        try:
            result = self._execute(task)
            task.result = result
            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now()
            self.memory.update_task_status(task.task_id, TaskStatus.COMPLETED, result=result)
            self.log.success(f"Task {task.task_id[:8]}... completed")

        except Exception as e:
            error_msg = str(e)
            task.status = TaskStatus.FAILED
            task.error = error_msg
            self.memory.update_task_status(task.task_id, TaskStatus.FAILED, error=error_msg)
            self.log.error(f"Task {task.task_id[:8]}... failed → {error_msg}")

        return task
