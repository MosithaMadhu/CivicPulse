"""REUSABLE DECISION PATTERN: incident triage policy  (#policy)

What it does
    Classifies a city infrastructure incident against a five-level priority policy and returns
    allow / review / block before any dispatch action is taken.  The Decider reads the incident
    description, location, sensor summary and incident type, then decides which priority category
    best fits, whether the evidence is sufficient to act without human review, and whether a
    specialist crew is needed.

Typed questions and options
    priority_category (choice): one of five priority levels, each with a one-line definition.
    confidence_adequate (noul): is the evidence sufficient to act without human review?
    requires_specialist (noul): does this incident need a specialist crew?

Wiring
    `IncidentTriagePolicy` extends `PolicyCheck`; agent/agent.py passes it to the Strands Agent
    as an `InterventionHandler`:
        policy = IncidentTriagePolicy(decider)
        Agent(..., interventions=[policy])
    It is called once per valid, non-safety-overridden incident.  The log entries are used by
    `HackathonAgent.chat()` to populate the `decisions` field.

How to reuse it
    Copy this file and change POLICY, CATEGORIES and QUESTIONS for your domain.  The Verdict
    mapping in IncidentTriagePolicy.triage_result() translates Decider output to your priority
    vocabulary.  Thresholds (block_at, review_at) are passed to PolicyCheck.__init__.
"""

from __future__ import annotations

from typing import Any

from strands.interventions import InterventionHandler, Proceed

from decider_kit import Decider
from decider_kit.patterns.policy import PolicyCheck, Verdict

PATTERN_TAG = "#policy"

POLICY = """
City infrastructure incidents must be triaged by urgency and public safety impact.
immediate_dispatch: risk to life, structural failure, or active flooding — act within the hour.
same_day: significant disruption or escalation risk — respond today.
scheduled: routine maintenance — plan within the week.
monitor: low severity, no immediate action needed — log and watch.
invalid: incomplete, unrecognisable, or test report — do not dispatch.
"""

CATEGORIES = {
    "immediate_dispatch": "risk to life, structural failure, active flooding, or blocked emergency routes",
    "same_day": "significant disruption, risk of escalation, or public inconvenience affecting many people",
    "scheduled": "routine defect requiring planned maintenance within the week",
    "monitor": "minor issue, no immediate risk, track for recurrence",
    "invalid": "report is incomplete, unrecognisable, contradictory, or a test submission",
}

QUESTIONS = {
    "priority_category": Decider.choice(
        "Which priority category best describes this infrastructure incident?",
        CATEGORIES,
    ),
    "confidence_adequate": Decider.noul(
        "Is the available evidence sufficient to act on this incident without human review?",
        {
            "true": "the incident type, location and description provide enough context to assign a clear priority",
            "false": "the description is vague, contradictory, or the sensor data is missing for a sensor-dependent incident type",
        },
    ),
    "requires_specialist": Decider.noul(
        "Does this incident require a specialist crew (structural engineer, utilities crew, etc.)?",
        {
            "true": "bridge, water main, electrical infrastructure, or other specialist domain",
            "false": "general maintenance crew can handle this: pothole, blocked drain, streetlight swap",
        },
    ),
}


class IncidentTriagePolicy(PolicyCheck, InterventionHandler):
    """PolicyCheck wired for city infrastructure incident triage.

    Maps the Decider's Verdict back to the CivicPulse priority vocabulary and exposes
    `triage_result()` for use by tools.py without going through the InterventionHandler path.
    """

    name = "IncidentTriagePolicy"  # required by InterventionHandler abstract interface

    def __init__(self, decider: Decider, block_at: float = 0.7, review_at: float = 0.4):
        super().__init__(decider, POLICY, CATEGORIES, block_at=block_at, review_at=review_at)
        # Override the base-class log with our own so PolicyCheck.check() entries
        # are captured here but we replace them with our enriched format below.
        self.log: list[dict[str, Any]] = []

    def check_incident(self, state: str, incident_id: str) -> dict[str, Any]:
        """Run the Decider on *state* and return a structured result dict."""
        import time

        before = len(self.log)
        t0 = time.perf_counter()
        verdict: Verdict = self.check(state)
        elapsed_ms = round((time.perf_counter() - t0) * 1000)

        # Remove any raw base-class entries appended by PolicyCheck.check()
        del self.log[before:]

        confident = verdict.confident
        category = verdict.category or "invalid"
        # force review when the Decider is not confident
        action = verdict.action if confident else "review"

        entry: dict[str, Any] = {
            "pattern": PATTERN_TAG,
            "incident_id": incident_id,
            "category": category,
            "p_violation": round(verdict.p_violation, 3),
            "severity": verdict.severity,
            "confident": confident,
            "verdict": action,
            "decider_ms": elapsed_ms,
        }
        self.log.append(entry)
        return entry

    # InterventionHandler stubs — the policy runs explicitly from tools.py, not via
    # before_tool_call, so these are pass-through.
    def before_invocation(self, event, **kwargs):
        return Proceed()

    def before_tool_call(self, event, **kwargs):
        return Proceed()
