"""
memory.py — In-process agent memory for the ALDO Returns Detective.

Keeps one Strands Agent instance per case in a module-level dict so that
conversation history is preserved across HTTP requests within a single
server process.

Public API:
  get_or_create_assistant(case_id, tools) → Agent
  rehydrate_from_db(case_id, db)          → Agent  (rebuilds history from DB)
  clear_agent(case_id)                    → None   (call after case is finalised)
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy import text
from strands import Agent

from .assistant import build_assistant

# ---------------------------------------------------------------------------
# Module-level store — one Agent per live case
# ---------------------------------------------------------------------------

_active_agents: dict[str, Agent] = {}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_or_create_assistant(case_id: str, tools: list[Any]) -> Agent:
    """
    Return the existing Agent for *case_id*, or create a fresh one.

    Args:
        case_id: Unique identifier for the return case.
        tools:   Tool list to pass to the agent (ignored if agent already exists).

    Returns:
        A Strands Agent with the Return Assistant system prompt.
    """
    if case_id not in _active_agents:
        _active_agents[case_id] = build_assistant(tools=tools)
    return _active_agents[case_id]


def rehydrate_from_db(case_id: str, db: Session) -> Agent:
    """
    Rebuild the Agent for *case_id* from persisted message history in the DB.

    Queries the ``case_messages`` table (columns: role, content, created_at)
    and reconstructs the Strands message format.  Replaces any existing
    in-memory agent for this case.

    Args:
        case_id: The case whose history should be reloaded.
        db:      Open SQLAlchemy session.

    Returns:
        A Strands Agent pre-loaded with the persisted conversation history.
    """
    rows = db.execute(
        text(
            "SELECT role, content "
            "FROM case_messages "
            "WHERE case_id = :cid "
            "ORDER BY created_at ASC"
        ),
        {"cid": case_id},
    ).fetchall()

    messages: list[dict] = [
        {"role": row[0], "content": [{"text": row[1]}]}
        for row in rows
    ]

    agent = build_assistant(messages=messages)
    _active_agents[case_id] = agent
    return agent


def clear_agent(case_id: str) -> None:
    """
    Remove the Agent for *case_id* from the in-process store.

    Call this after a case has been finalised (approved / rejected / escalated)
    to free memory and prevent stale history from leaking into new sessions.

    Args:
        case_id: The case to evict from the store.
    """
    _active_agents.pop(case_id, None)
