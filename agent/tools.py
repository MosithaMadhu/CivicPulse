"""CivicPulse tools: incident validation, threshold checks, triage, explanation, and dispatch.

Every tool is a Strands @tool function. triage_incident and generate_explanation are called by
the agent automatically; dispatch_action requires explicit operator approval.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from strands import tool

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_TYPES = {
    "blocked_drain",
    "bridge_vibration",
    "streetlight_failure",
    "pothole",
    "water_leak",
}

PRIORITY_VALUES = {
    "immediate_dispatch",
    "same_day",
    "scheduled",
    "monitor",
    "invalid",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class IncidentReport:
    incident_id: str
    incident_type: str          # one of VALID_TYPES
    location: str
    reported_at: str            # ISO-8601
    description: str
    sensor_readings: dict[str, float] = field(default_factory=dict)
    synthetic_data: bool = False


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_REQUIRED_FIELDS = ["incident_id", "incident_type", "location", "reported_at", "description"]


def validate_incident(data: dict) -> IncidentReport:
    """Parse and validate a raw incident dict, raising ValueError with the field name on failure."""
    for f in _REQUIRED_FIELDS:
        if not data.get(f):
            raise ValueError(f)
    incident_type = data["incident_type"]
    if incident_type not in VALID_TYPES:
        raise ValueError(f"incident_type must be one of {sorted(VALID_TYPES)}, got {incident_type!r}")
    return IncidentReport(
        incident_id=str(data["incident_id"]),
        incident_type=incident_type,
        location=str(data["location"]),
        reported_at=str(data["reported_at"]),
        description=str(data["description"]),
        sensor_readings={k: float(v) for k, v in data.get("sensor_readings", {}).items()},
        synthetic_data=bool(data.get("synthetic_data", False)),
    )


# ---------------------------------------------------------------------------
# Threshold table (deterministic, no model calls)
# ---------------------------------------------------------------------------

THRESHOLDS: dict[str, dict[str, float]] = {
    "blocked_drain":       {"water_level_m": 0.9},
    "bridge_vibration":    {"vibration_mm_s": 5.0},
    "water_leak":          {"flow_rate_l_min": 50.0},
    "streetlight_failure": {},
    "pothole":             {},
}


def check_thresholds(report: IncidentReport) -> tuple[bool, list[str]]:
    """Return (safety_override, triggered_thresholds). Pure code, no model calls."""
    thresholds = THRESHOLDS.get(report.incident_type, {})
    triggered: list[str] = []
    for sensor_key, limit in thresholds.items():
        value = report.sensor_readings.get(sensor_key)
        if value is not None and value >= limit:
            triggered.append(sensor_key)
    return bool(triggered), triggered


# ---------------------------------------------------------------------------
# State string builder
# ---------------------------------------------------------------------------

def _sensor_summary(report: IncidentReport) -> str:
    """Build a human-readable sensor summary from the report's sensor_readings."""
    if not report.sensor_readings:
        return "no sensor data available"
    parts = [f"{k}={v}" for k, v in report.sensor_readings.items()]
    return ", ".join(parts)


def build_state_string(report: IncidentReport, sensor_summary: str) -> str:
    """Build the state string passed to PolicyCheck.check()."""
    synthetic_note = " [SYNTHETIC DATA]" if report.synthetic_data else ""
    return (
        f"Incident type: {report.incident_type}\n"
        f"Location: {report.location}\n"
        f"Reported at: {report.reported_at}\n"
        f"Description: {report.description}\n"
        f"Sensor readings: {sensor_summary}{synthetic_note}"
    )


# ---------------------------------------------------------------------------
# Module-level policy instance (set by HackathonAgent on startup)
# ---------------------------------------------------------------------------

# The policy is injected by HackathonAgent so tests can supply a FakeDecider.
# Tools look it up at call time to avoid import-order issues.
_policy_instance: Any = None


def set_policy(policy: Any) -> None:
    """Called by HackathonAgent to inject the IncidentTriagePolicy for this session."""
    global _policy_instance
    _policy_instance = policy


def _get_policy() -> Any:
    if _policy_instance is None:
        raise RuntimeError("Policy not initialised. Call set_policy() before using triage_incident.")
    return _policy_instance


# ---------------------------------------------------------------------------
# Strands tools
# ---------------------------------------------------------------------------

@tool
def triage_incident(incident_json: str) -> dict:
    """Validate and triage an infrastructure incident report, returning a structured priority decision."""
    # 1. Parse input
    try:
        data = json.loads(incident_json)
    except json.JSONDecodeError as e:
        return {"error_type": "parse_error", "error_message": str(e), "priority": "invalid"}

    # 2. Validate
    try:
        report = validate_incident(data)
    except ValueError as e:
        return {
            "error_type": "validation_error",
            "error_message": str(e),
            "priority": "invalid",
            "incident_id": data.get("incident_id", "unknown"),
        }

    # 3. Threshold check
    safety_override, triggered = check_thresholds(report)
    sensor_sum = _sensor_summary(report)

    if safety_override:
        return {
            "incident_id": report.incident_id,
            "incident_type": report.incident_type,
            "location": report.location,
            "reported_at": report.reported_at,
            "description": report.description,
            "sensor_summary": sensor_sum,
            "priority": "immediate_dispatch",
            "verdict_action": "allow",
            "p_violation": 1.0,
            "severity": "critical",
            "confident": True,
            "safety_override": True,
            "triggered_thresholds": triggered,
            "requires_specialist": True,
            "decider_ms": 0,
            "synthetic_data": report.synthetic_data,
            "error_message": None,
        }

    # 4. PolicyCheck via injected policy
    policy = _get_policy()
    state = build_state_string(report, sensor_sum)
    t0 = time.perf_counter()
    entry = policy.check_incident(state, report.incident_id)
    decider_ms = entry.get("decider_ms", round((time.perf_counter() - t0) * 1000))

    # Map Decider category to priority (closed set)
    category = entry.get("category", "invalid")
    priority = category if category in PRIORITY_VALUES else "invalid"

    return {
        "incident_id": report.incident_id,
        "incident_type": report.incident_type,
        "location": report.location,
        "reported_at": report.reported_at,
        "description": report.description,
        "sensor_summary": sensor_sum,
        "priority": priority,
        "verdict_action": entry.get("verdict", "review"),
        "p_violation": entry.get("p_violation", 0.0),
        "severity": entry.get("severity", "none"),
        "confident": entry.get("confident", False),
        "safety_override": False,
        "triggered_thresholds": triggered,
        "requires_specialist": True,
        "decider_ms": decider_ms,
        "synthetic_data": report.synthetic_data,
        "error_message": None,
    }


@tool
def generate_explanation(triage_result_json: str) -> dict:
    """Generate a plain-language explanation and maintenance recommendation for a triage result."""
    try:
        result = json.loads(triage_result_json)
    except json.JSONDecodeError as e:
        return {"explanation": "Unable to parse triage result.", "proposed_action": "Review manually.", "model_id": "unknown"}

    incident_type = result.get("incident_type", "unknown")
    location = result.get("location", "unknown")
    description = result.get("description", "")
    sensor_summary = result.get("sensor_summary", "no sensor data")
    priority = result.get("priority", "unknown")
    severity = result.get("severity", "none")
    requires_specialist = result.get("requires_specialist", False)
    safety_override = result.get("safety_override", False)
    synthetic_note = " (Note: this report contains synthetic/test data.)" if result.get("synthetic_data") else ""

    override_note = " A safety threshold was exceeded — immediate action is required." if safety_override else ""
    specialist_note = " A specialist crew is required." if requires_specialist else " A general maintenance crew can handle this."

    prompt = (
        f"You are a city infrastructure triage assistant. Explain the following triage decision in plain English "
        f"and suggest a concrete maintenance action.{synthetic_note}\n\n"
        f"Incident type: {incident_type}\n"
        f"Location: {location}\n"
        f"Description: {description}\n"
        f"Sensor readings: {sensor_summary}\n"
        f"Priority: {priority}\n"
        f"Severity: {severity}\n"
        f"{override_note}{specialist_note}\n\n"
        f"Provide:\n"
        f"1. A two-sentence explanation of why this priority was assigned.\n"
        f"2. A specific proposed maintenance action (one sentence).\n"
    )

    try:
        from decider_kit.llm import ask, default_model_id
        text, usage = ask(prompt)
        model_id = usage.get("model_id", default_model_id())
    except Exception:
        text = (
            f"This {incident_type} incident at {location} has been assigned priority '{priority}' "
            f"due to its severity level of '{severity}'.{override_note}"
        )
        proposed = f"Schedule a {'specialist' if requires_specialist else 'maintenance'} crew response appropriate for '{priority}' priority."
        return {"explanation": text, "proposed_action": proposed, "model_id": "unavailable"}

    # Split text into explanation and proposed action if possible
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    if len(lines) >= 2:
        explanation = " ".join(lines[:-1])
        proposed_action = lines[-1]
    else:
        explanation = text.strip()
        proposed_action = f"Respond according to '{priority}' priority protocol."

    return {"explanation": explanation, "proposed_action": proposed_action, "model_id": model_id}


@tool
def dispatch_action(incident_id: str, approved_by: str, approved_at: str, action: str) -> dict:
    """Dispatch a maintenance action for an incident. Requires explicit operator approval — never called automatically."""
    if not approved_by or not approved_by.strip():
        return {
            "error_type": "missing_approval",
            "error_message": "approved_by must be a non-empty operator name. This action requires explicit human approval.",
            "dispatched": False,
            "incident_id": incident_id,
        }
    return {
        "dispatched": True,
        "incident_id": incident_id,
        "action": action,
        "approved_by": approved_by.strip(),
        "approved_at": approved_at,
    }


# ---------------------------------------------------------------------------
# Exported tool list
# ---------------------------------------------------------------------------

TOOLS = [triage_incident, generate_explanation, dispatch_action]
