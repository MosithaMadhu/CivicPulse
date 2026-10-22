# Design Document — CivicPulse

## Overview

CivicPulse replaces the kit example agent (online-store support) with an infrastructure incident
triage agent. The `agent/` directory is fully rewritten; the kit contracts in
`agent/agent.py` (`NAME`, `HackathonAgent.chat`) and `agent/decision.py` (`PATTERN_TAG`,
`QUESTIONS`, docstring) are preserved exactly. `decider_kit/`, `demo/`, `scripts/`, and
`deploy/` are unchanged.

The system uses the **Policy** pattern (`decider_kit.patterns.policy.PolicyCheck`) to classify
every valid, non-safety-overridden incident into one of five priority categories. A generative
LLM (Ollama locally, Amazon Bedrock in production) then produces a plain-language explanation
and maintenance recommendation. A human must approve any dispatch action.

---

## Architecture

```
User / demo UI (demo/server.py + demo/static/index.html)
        │ POST /api/chat  { message: "<json incident>" }
        ▼
agent/agent.py  HackathonAgent.chat()
    │
    ├─ 1. triage_incident tool  (agent/tools.py)
    │       ├─ validate_incident()      → IncidentReport | ValidationError
    │       ├─ check_thresholds()       → safety_override flag
    │       └─ PolicyCheck.check()      → Verdict   [Strands Decider 2B]
    │
    ├─ 2. generate_explanation tool  (agent/tools.py)
    │       └─ decider_kit.llm.ask()   → Explanation   [LLM]
    │
    └─ 3. dispatch_action tool  (agent/tools.py)
            └─ requires HumanApproval payload
```

The LLM (Strands Agent in `HackathonAgent`) calls the tools; the tools call the Decider and the
explanation LLM. Decision and explanation are separated: `triage_incident` returns a Verdict
before `generate_explanation` is called.

---

## Component Design

### agent/tools.py

#### Data models (pure Python dataclasses)

```python
@dataclass
class IncidentReport:
    incident_id: str
    incident_type: str          # one of VALID_TYPES
    location: str
    reported_at: str            # ISO-8601
    description: str
    sensor_readings: dict[str, float]   # may be empty
    synthetic_data: bool = False

VALID_TYPES = {"blocked_drain", "bridge_vibration", "streetlight_failure", "pothole", "water_leak"}
PRIORITY_VALUES = {"immediate_dispatch", "same_day", "scheduled", "monitor", "invalid"}
```

#### Threshold table (deterministic, no model)

```python
THRESHOLDS: dict[str, dict[str, float]] = {
    "blocked_drain":      {"water_level_m": 0.9},
    "bridge_vibration":   {"vibration_mm_s": 5.0},
    "water_leak":         {"flow_rate_l_min": 50.0},
    "streetlight_failure": {},     # no sensor thresholds
    "pothole":             {},
}
```

`check_thresholds(report)` returns `(safety_override: bool, triggered: list[str])`.
Exceeding any threshold → `safety_override=True`. No external calls.

#### `@tool triage_incident(incident_json: str) -> dict`

1. Parse and validate `incident_json` → `IncidentReport`.
2. Run `check_thresholds()`.
3. If `safety_override=True`: return triage result with `priority=immediate_dispatch`,
   `safety_override=True`, no Decider call.
4. Otherwise: build state string, call `POLICY_CHECK.check(state)`, map Verdict to priority.
5. Return structured triage result dict.

Triage result schema:
```json
{
  "incident_id": "str",
  "incident_type": "str",
  "location": "str",
  "reported_at": "str",
  "description": "str",
  "sensor_summary": "str",
  "priority": "immediate_dispatch|same_day|scheduled|monitor|invalid",
  "verdict_action": "allow|review|block",
  "p_violation": 0.0,
  "severity": "none|minor|serious|critical",
  "confident": true,
  "safety_override": false,
  "triggered_thresholds": [],
  "requires_specialist": true,
  "decider_ms": 0,
  "synthetic_data": false,
  "error_message": null
}
```

#### `@tool generate_explanation(triage_result_json: str) -> dict`

1. Parse triage result.
2. Build LLM prompt from `incident_type`, `location`, `description`, `sensor_summary`,
   `priority`, `severity`, `requires_specialist`, `safety_override`.
3. Call `decider_kit.llm.ask(prompt)`.
4. Return `{"explanation": str, "proposed_action": str, "model_id": str}`.
5. On LLM failure: return placeholder explanation, do not raise.

#### `@tool dispatch_action(incident_id: str, approved_by: str, approved_at: str, action: str) -> dict`

1. Validate `approved_by` is non-empty string.
2. If invalid: return error dict, do not dispatch.
3. If valid: return `{"dispatched": True, "incident_id": ..., "action": ..., "approved_by": ..., "approved_at": ...}`.
4. Never called automatically; only on explicit operator instruction.

---

### agent/decision.py

```python
"""REUSABLE DECISION PATTERN: incident triage policy  (#policy)
...
"""
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
        "Which priority category best describes this infrastructure incident?", CATEGORIES),
    "confidence_adequate": Decider.noul(
        "Is the available evidence sufficient to act on this incident without human review?",
        {"true": "the incident type, location and description provide enough context to assign a clear priority",
         "false": "the description is vague, contradictory, or the sensor data is missing for a sensor-dependent incident type"}),
    "requires_specialist": Decider.noul(
        "Does this incident require a specialist crew (structural engineer, utilities crew, etc.)?",
        {"true": "bridge, water main, electrical infrastructure, or other specialist domain",
         "false": "general maintenance crew can handle this: pothole, blocked drain, streetlight swap"}),
}
```

`IncidentTriagePolicy` extends `PolicyCheck` with CivicPulse POLICY and CATEGORIES.
Priority mapping from Verdict:
- `safety_override=True` → `immediate_dispatch`
- Verdict `category` → priority directly (closed set)
- `confident=False` → force `verdict_action=review`

---

### agent/agent.py

```python
NAME = "CivicPulse — City Infrastructure Triage"
SYSTEM_PROMPT = """You are CivicPulse, a city infrastructure triage assistant.
When given an incident report (JSON or plain description), call triage_incident to assess it,
then call generate_explanation to produce a plain-language explanation and recommendation.
Present the triage result clearly. Do not call dispatch_action unless the operator explicitly
approves with their name and the current timestamp."""
```

`HackathonAgent`:
- Constructor: `Decider`, `model_id`, `eager` (not used for CivicPulse; kept for contract).
- `_session(session_id)`: creates Strands `Agent` with `[triage_incident, generate_explanation,
  dispatch_action]` tools and `IncidentTriagePolicy` as an `InterventionHandler`.
  The `InterventionHandler` logs each `PolicyCheck` call to `guard.log` in the required
  `decisions` format.
- `chat()`: returns `reply`, `decisions`, `model_id`, `latency_ms`.

`decisions` list item schema (one per Decider call):
```json
{
  "pattern": "#policy",
  "incident_id": "str",
  "category": "str",
  "p_violation": 0.0,
  "severity": "str",
  "confident": true,
  "verdict": "allow|review|block",
  "decider_ms": 0
}
```

---

### agent/AGENT.md

Documents:
- Pitch, audience, features.
- Decision handed to Strands Decider 2B (incident triage policy, `#policy`).
- Local substitutes vs Bedrock: `LLM_PROVIDER=local` → Ollama; `LLM_PROVIDER=bedrock` → Amazon Bedrock.
- `DECIDER_MODE=local` → local server; `DECIDER_MODE=ugmdu` → hosted API.

---

### agent/eval_cases.jsonl

20+ cases, `tune` / `test` split. Each case:
```json
{"state": "<state string passed to PolicyCheck.check()>", "expected": {"priority_category": "same_day", "confidence_adequate": "yes", "requires_specialist": "no"}, "split": "tune"}
```

Cases cover: blocked drain above threshold (safety override — handled before Decider, so state
notes override), bridge vibration below threshold, streetlight outage no sensor, water leak
above threshold, pothole citizen report, ambiguous/incomplete reports, invalid type, and
confidence edge cases.

---

### agent/demo_script.yaml

Four turns mapping to the four mandatory scenarios:

| Turn | Scenario | Key behaviours shown |
|------|----------|----------------------|
| A | Blocked drain, water_level_m=0.92 | safety_override=True, immediate_dispatch, no Decider call |
| B | Bridge vibration, vibration_mm_s=4.2 | Decider called, same_day or scheduled, confidence≥0.6 |
| C | Streetlight outage, no sensor data | Decider called, same_day or scheduled |
| D | Invalid report, missing incident_type | validation error, priority=invalid |

Each turn narration states the data is synthetic.

---

### agent/features.yaml

```yaml
memory:
  enabled: false
gateway:
  enabled: false
```

CivicPulse is a triage tool; it does not need cross-session memory or external tool gateway for
the prototype.

---

## Local vs Production Configuration

| Component | Local (LLM_PROVIDER=local, DECIDER_MODE=local) | Production (LLM_PROVIDER=bedrock) |
|-----------|------------------------------------------------|-----------------------------------|
| LLM | Ollama (`qwen3.5:4b` default) | Amazon Bedrock (`us.amazon.nova-pro-v1:0` default) |
| Decider | Local server at DECIDER_URL | Hosted API or local, via `decider_kit.Decider` |
| Credentials | None required | AWS profile `hackathon` |
| `model_id` in response | `qwen3.5:4b` or configured | `us.amazon.nova-pro-v1:0` or configured |

The demo UI, API contract, and all tests are identical in both configurations.

---

## Test Strategy

Tests live in `tests/test_tools.py` (existing file, extended) and a new
`tests/test_civic_pulse.py`.

Property-based tests (using `FakeDecider` from `tests/fakes.py`):
- Round-trip: serialise/deserialise `IncidentReport` → equal.
- Monotonicity: any reading ≥ threshold → priority is always `immediate_dispatch`.
- Closed set: Verdict action always in `{allow, review, block}`.
- Closed set: priority always in five values.
- Human gate: `dispatch_action` without `approved_by` always returns error, never dispatches.
- Error resilience: validation errors always return structured response, never raise.
- Ordering: Explanation is only generated after a Verdict exists (tool call order enforced by
  agent; unit test mocks both tools and checks order).

All tests run with `LLM_PROVIDER=local` and `FakeDecider`; no AWS credentials or live models
needed.

---

## API Response Shape

`POST /api/chat` returns the standard `HackathonAgent.chat()` dict:

```json
{
  "reply": "Incident INC-001 has been triaged...",
  "decisions": [
    {
      "pattern": "#policy",
      "incident_id": "INC-001",
      "category": "same_day",
      "p_violation": 0.62,
      "severity": "serious",
      "confident": true,
      "verdict": "allow",
      "decider_ms": 180
    }
  ],
  "model_id": "qwen3.5:4b",
  "latency_ms": 1240,
  "agent": "CivicPulse — City Infrastructure Triage",
  "eager": false
}
```

The dashboard `decisions` panel renders each decision dict automatically via the existing
`demo/static/index.html` JavaScript (no UI changes required).

The `synthetic_data` flag is included in the triage result within `reply` (as formatted text
or JSON) and in the structured tool return from `triage_incident`. The operator sees it in the
reply; the `SYNTHETIC DATA` label appears when the agent surfaces it.

---

## Assumptions and Open Questions

1. **`PolicyCheck` categories parameter**: the `policy.questions()` function takes `categories`
   as a `dict[str, str]`. We pass `CATEGORIES` directly. The Verdict `category` field is one
   of the keys — confirmed by reading `policy.py`.

2. **`decisions` log format**: `PolicyCheck.log` records `stage`, `action`, `category`,
   `p_violation`, `severity`, `decider_ms`. The `HackathonAgent` adapter adds `pattern`,
   `incident_id`, `confident`, `verdict` to match the demo UI's expected fields.

3. **`FakeDecider` compatibility**: `FakeDecider` in `tests/fakes.py` supports `choice` and
   `noul` question types — confirmed. Our QUESTIONS use both.

4. **`dispatch_action` as a Strands `@tool`**: the tool receives `approved_by` as a plain
   string parameter. The `HumanApproval` concept maps to function parameters, not a separate
   dataclass, to keep the Strands `@tool` signature simple.

5. **Demo UI changes**: none required. The existing `demo/static/index.html` renders `decisions`
   dicts generically. The `synthetic_data` notice is surfaced in the agent's reply text.

6. **`IS_KIT_EXAMPLE`**: must be absent from `agent/decision.py`. The submission check in
   `scripts/check_submission.py` fails if it is present.
