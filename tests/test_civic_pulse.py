"""Property-based tests for CivicPulse.

All tests run with FakeDecider — no AWS credentials or live models needed.
Run with: .venv\\Scripts\\python.exe -m pytest tests/test_civic_pulse.py -q
"""

from __future__ import annotations

import json
from dataclasses import asdict
from unittest.mock import patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from fakes import FakeDecider

# ---------------------------------------------------------------------------
# Import the modules under test
# ---------------------------------------------------------------------------
from agent.tools import (
    PRIORITY_VALUES,
    THRESHOLDS,
    VALID_TYPES,
    IncidentReport,
    check_thresholds,
    dispatch_action,
    generate_explanation,
    set_policy,
    triage_incident,
    validate_incident,
)
from agent.decision import IncidentTriagePolicy, QUESTIONS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_report(**overrides) -> dict:
    base = {
        "incident_id": "INC-001",
        "incident_type": "pothole",
        "location": "Main St",
        "reported_at": "2026-10-22T09:00:00Z",
        "description": "Large pothole on the corner.",
        "sensor_readings": {},
        "synthetic_data": False,
    }
    base.update(overrides)
    return base


def _install_fake_policy(rule=None) -> IncidentTriagePolicy:
    policy = IncidentTriagePolicy(FakeDecider(rule))
    set_policy(policy)
    return policy


# ---------------------------------------------------------------------------
# 7.1 Round-trip invariant
# ---------------------------------------------------------------------------

@given(
    incident_id=st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"))),
    incident_type=st.sampled_from(sorted(VALID_TYPES)),
    location=st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd", "Zs"))),
    reported_at=st.just("2026-10-22T09:00:00Z"),
    description=st.text(min_size=1, max_size=100, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd", "Zs", "Po"))),
    synthetic_data=st.booleans(),
)
@settings(max_examples=50)
def test_round_trip(incident_id, incident_type, location, reported_at, description, synthetic_data):
    """IncidentReport → dict → validate_incident → equal fields."""
    original = IncidentReport(
        incident_id=incident_id,
        incident_type=incident_type,
        location=location,
        reported_at=reported_at,
        description=description,
        sensor_readings={},
        synthetic_data=synthetic_data,
    )
    data = asdict(original)
    restored = validate_incident(data)
    assert restored.incident_id == original.incident_id
    assert restored.incident_type == original.incident_type
    assert restored.location == original.location
    assert restored.description == original.description
    assert restored.synthetic_data == original.synthetic_data


# ---------------------------------------------------------------------------
# 7.2 Monotonicity: reading >= threshold → safety_override=True
# ---------------------------------------------------------------------------

@given(
    incident_type=st.sampled_from(["blocked_drain", "bridge_vibration", "water_leak"]),
    multiplier=st.floats(min_value=1.0, max_value=10.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=50)
def test_threshold_monotonicity(incident_type, multiplier):
    """Any sensor reading >= threshold always yields safety_override=True."""
    sensor_key, limit = next(iter(THRESHOLDS[incident_type].items()))
    reading = limit * multiplier  # always >= limit since multiplier >= 1.0
    report = IncidentReport(
        incident_id="INC-X",
        incident_type=incident_type,
        location="Test",
        reported_at="2026-10-22T09:00:00Z",
        description="Test incident",
        sensor_readings={sensor_key: reading},
    )
    override, triggered = check_thresholds(report)
    assert override is True
    assert sensor_key in triggered


# ---------------------------------------------------------------------------
# 7.3 Safety override → priority=immediate_dispatch (no Decider call)
# ---------------------------------------------------------------------------

def test_safety_override_maps_to_immediate_dispatch():
    """safety_override=True always yields priority=immediate_dispatch and no Decider call."""
    policy = _install_fake_policy()
    fake_decider: FakeDecider = policy.decider  # type: ignore
    incident = _make_report(
        incident_type="blocked_drain",
        sensor_readings={"water_level_m": 1.5},  # > 0.9 threshold
    )
    result = triage_incident(json.dumps(incident))
    assert result["priority"] == "immediate_dispatch"
    assert result["safety_override"] is True
    assert len(fake_decider.calls) == 0


# ---------------------------------------------------------------------------
# 7.4 Closed-set: Verdict action always in {allow, review, block}
# ---------------------------------------------------------------------------

@given(incident_type=st.sampled_from(sorted(VALID_TYPES)))
@settings(max_examples=30)
def test_verdict_action_closed_set(incident_type):
    """Decider verdict action is always allow, review, or block."""
    _install_fake_policy()
    incident = _make_report(incident_type=incident_type)
    result = triage_incident(json.dumps(incident))
    assert result["verdict_action"] in {"allow", "review", "block"}


# ---------------------------------------------------------------------------
# 7.5 Closed-set: triage result priority always in PRIORITY_VALUES
# ---------------------------------------------------------------------------

@given(incident_type=st.sampled_from(sorted(VALID_TYPES)))
@settings(max_examples=30)
def test_priority_in_closed_set(incident_type):
    """triage_incident result priority is always a valid priority value."""
    _install_fake_policy()
    incident = _make_report(incident_type=incident_type)
    result = triage_incident(json.dumps(incident))
    assert result["priority"] in PRIORITY_VALUES


# ---------------------------------------------------------------------------
# 7.6 Human-gate: dispatch_action with empty approved_by always errors
# ---------------------------------------------------------------------------

@given(
    incident_id=st.text(min_size=1, max_size=20),
    approved_by=st.one_of(st.just(""), st.just("   ")),
    action=st.text(min_size=1, max_size=50),
)
@settings(max_examples=30)
def test_dispatch_requires_approved_by(incident_id, approved_by, action):
    """dispatch_action with empty approved_by always returns error dict, never dispatches."""
    result = dispatch_action(incident_id, approved_by, "2026-10-22T09:00:00Z", action)
    assert result.get("dispatched") is False
    assert "error_type" in result
    assert result.get("error_type") == "missing_approval"


# ---------------------------------------------------------------------------
# 7.7 Error resilience: missing required field → structured error, no exception
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("missing_field", ["incident_id", "incident_type", "location", "reported_at", "description"])
def test_missing_field_returns_error_dict(missing_field):
    """Missing required field returns error dict with error_type and error_message, never raises."""
    _install_fake_policy()
    incident = _make_report()
    del incident[missing_field]
    result = triage_incident(json.dumps(incident))
    assert "error_type" in result
    assert "error_message" in result
    assert result["priority"] == "invalid"


def test_invalid_incident_type_returns_error_dict():
    """Unknown incident_type returns error dict, never raises."""
    _install_fake_policy()
    incident = _make_report(incident_type="flying_car")
    result = triage_incident(json.dumps(incident))
    assert "error_type" in result


# ---------------------------------------------------------------------------
# 7.8 Decider not called when safety_override=True
# ---------------------------------------------------------------------------

def test_decider_not_called_on_safety_override():
    """When safety_override is triggered, FakeDecider.calls must be empty."""
    policy = _install_fake_policy()
    fake_decider: FakeDecider = policy.decider  # type: ignore
    incident = _make_report(
        incident_type="water_leak",
        sensor_readings={"flow_rate_l_min": 100.0},  # > 50.0 threshold
    )
    before = len(fake_decider.calls)
    triage_incident(json.dumps(incident))
    assert len(fake_decider.calls) == before  # no new calls


# ---------------------------------------------------------------------------
# 7.9 confident=False from Decider forces verdict_action=review
# ---------------------------------------------------------------------------

def test_low_confidence_forces_review():
    """When confidence_adequate is false (p_yes < 0.5), verdict_action must be review."""
    # PolicyCheck.check() uses its own internal questions (category, violates, severity).
    # We set violates p_yes very low (0.1) so action=allow, but confidence < 0.7 so
    # check_incident() reads confident=False and forces verdict_action=review.
    # The rule must handle choice → return a valid key, score → return a valid level name.
    def low_confidence_rule(state, name, q):
        if name == "violates":
            return 0.1  # noul: p_yes=0.1, confidence=0.9 — but we want low confidence on category
        if name == "category":
            return "same_day"  # choice
        if q["type"] == "score":
            return q["criteria"][0]  # first level
        return 0.9

    # Patch PolicyCheck.decide to return a Verdict with confident=False
    from decider_kit.patterns.policy import Verdict as PolicyVerdict

    def fake_decide(self, a):
        return PolicyVerdict(
            action="allow",
            category="same_day",
            p_violation=0.1,
            severity="none",
            confident=False,  # force low confidence
            answers=a,
        )

    import decider_kit.patterns.policy as policy_mod
    original_decide = policy_mod.PolicyCheck.decide

    _install_fake_policy(low_confidence_rule)
    incident = _make_report(incident_type="pothole")

    with patch.object(policy_mod.PolicyCheck, "decide", fake_decide):
        result = triage_incident(json.dumps(incident))
    assert result["verdict_action"] == "review"
    assert result["confident"] is False


# ---------------------------------------------------------------------------
# 7.10 generate_explanation returns non-empty string on LLM error
# ---------------------------------------------------------------------------

def test_generate_explanation_survives_llm_error():
    """generate_explanation returns non-empty explanation and does not raise on LLM failure."""
    triage_result = {
        "incident_id": "INC-001",
        "incident_type": "pothole",
        "location": "Main St",
        "description": "Large pothole.",
        "sensor_summary": "no sensor data",
        "priority": "scheduled",
        "severity": "minor",
        "requires_specialist": False,
        "safety_override": False,
        "synthetic_data": False,
    }
    with patch("decider_kit.llm.ask", side_effect=RuntimeError("LLM unavailable")):
        result = generate_explanation(json.dumps(triage_result))
    assert isinstance(result, dict)
    assert result.get("explanation")
    assert len(result["explanation"]) > 0
    assert result.get("model_id") == "unavailable"
