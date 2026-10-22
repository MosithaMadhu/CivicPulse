"""CivicPulse: a city infrastructure triage agent.

The LLM (Amazon Bedrock by default) orchestrates tool calls; Strands Decider 2B classifies
every valid, non-safety-overridden incident via the #policy pattern before a dispatch action
is taken.

    from agent.agent import HackathonAgent
    from tests.fakes import FakeDecider
    out = HackathonAgent(decider=FakeDecider()).chat('{"incident_id": "INC-001", ...}')
    out["reply"], out["decisions"]
"""

from __future__ import annotations

import time
from typing import Any

from strands import Agent

from decider_kit import Decider
from decider_kit.llm import agent_model, default_model_id, image_blocks, strip_thinking

from .decision import IncidentTriagePolicy
from .tools import TOOLS, set_policy

NAME = "CivicPulse — City Infrastructure Triage"

SYSTEM_PROMPT = """You are CivicPulse, a city infrastructure triage assistant.
When given an incident report (JSON or plain description), call triage_incident to assess it,
then call generate_explanation to produce a plain-language explanation and recommendation.
Present the triage result clearly, including the priority, severity, and any specialist
requirements. If the data is marked as synthetic, mention that clearly.
Do not call dispatch_action unless the operator explicitly approves with their name and the
current timestamp."""


class HackathonAgent:
    """One Strands agent per session, with the #policy decision pattern wired in."""

    def __init__(self, decider: Decider | None = None, model_id: str | None = None, eager: bool | None = None):
        import os

        self.decider = decider or Decider()
        self.eager = eager if eager is not None else os.environ.get("DEMO_EAGER") == "1"
        self.model_id = model_id or default_model_id()
        self.sessions: dict[str, tuple[Agent, IncidentTriagePolicy]] = {}

    def _session(self, session_id: str) -> tuple[Agent, IncidentTriagePolicy]:
        if session_id not in self.sessions:
            policy = IncidentTriagePolicy(self.decider)
            # Inject policy into tools module so triage_incident can call it
            set_policy(policy)
            agent = Agent(
                model=agent_model(self.model_id),
                system_prompt=SYSTEM_PROMPT,
                tools=TOOLS,
                callback_handler=None,
            )
            self.sessions[session_id] = (agent, policy)
        return self.sessions[session_id]

    def chat(self, message: str, session_id: str = "default", images: list[Any] | None = None) -> dict[str, Any]:
        """`images` (optional): photos attached by the operator (paths, bytes or base64)."""
        agent, policy = self._session(session_id)
        # Ensure the per-session policy is active for this call
        set_policy(policy)
        before = len(policy.log)
        t0 = time.perf_counter()
        result = agent(
            [*image_blocks(images), {"text": message}] if images else message,
            invocation_state={"images": list(images or [])},
        )
        reply = strip_thinking(str(result))
        return {
            "reply": reply,
            "decisions": policy.log[before:],
            "model_id": self.model_id,
            "latency_ms": round((time.perf_counter() - t0) * 1000),
            "agent": NAME,
            "eager": self.eager,
        }


if __name__ == "__main__":
    import json
    import sys

    a = HackathonAgent()
    sample = '{"incident_id": "INC-001", "incident_type": "blocked_drain", "location": "Main St", "reported_at": "2026-10-22T09:00:00Z", "description": "Drain blocked, water rising.", "sensor_readings": {"water_level_m": 0.92}, "synthetic_data": true}'
    for msg in sys.argv[1:] or [sample]:
        print(json.dumps(a.chat(msg), indent=1))
