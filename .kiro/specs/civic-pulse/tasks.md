# Implementation Tasks — CivicPulse

## Task Overview

Tasks are ordered so each builds on the last. Every task includes a verification step that can
be run without AWS credentials (`LLM_PROVIDER=local`, `DECIDER_MODE=local` or mocked).
Do not install models, deploy, or overwrite existing files without reading them first.

---

- [x] 1. Inspect kit APIs and contracts
  - [x] 1.1 Read `decider_kit/patterns/policy.py` and confirm `PolicyCheck`, `Verdict`, and `questions()` signatures
  - [x] 1.2 Read `decider_kit/client.py` and confirm `Decider.choice`, `Decider.noul`, `Decider.score` builders
  - [x] 1.3 Read `tests/fakes.py` and confirm `FakeDecider` supports `choice` and `noul` question types
  - [x] 1.4 Read `tests/test_agent_contract.py` and note all assertions the new agent must satisfy
  - [x] 1.5 Read `agent/features.yaml` and confirm memory and gateway are both disabled for the kit example
  - Verification: no files are modified; inspection findings match the design doc assumptions

- [x] 2. Write `agent/decision.py` — reusable `#policy` decision pattern
  - [x] 2.1 Remove `IS_KIT_EXAMPLE` and `OrderActionGuard`; replace with CivicPulse policy content
  - [x] 2.2 Add module docstring headed `REUSABLE DECISION PATTERN: incident triage policy` covering what it does, typed questions, wiring, and how to reuse
  - [x] 2.3 Set `PATTERN_TAG = "#policy"`
  - [x] 2.4 Define `POLICY` string describing the five priority levels
  - [x] 2.5 Define `CATEGORIES` dict mapping each of the five Priority values to a one-line description
  - [x] 2.6 Define `QUESTIONS` dict with `priority_category` (choice), `confidence_adequate` (noul), `requires_specialist` (noul) using `Decider.choice` / `Decider.noul`
  - [x] 2.7 Define `IncidentTriagePolicy` class that wraps `PolicyCheck` with `POLICY` and `CATEGORIES`; maps Verdict to priority; forces `review` when `confident=False`
  - Verification: `python -c "from agent.decision import PATTERN_TAG, QUESTIONS, IncidentTriagePolicy; assert PATTERN_TAG == '#policy'; assert set(QUESTIONS) >= {'priority_category', 'confidence_adequate', 'requires_specialist'}"` passes

- [x] 3. Write `agent/tools.py` — incident data models, thresholds, and Strands tools
  - [x] 3.1 Define `VALID_TYPES`, `PRIORITY_VALUES` constants
  - [x] 3.2 Define `IncidentReport` dataclass with all required fields and `synthetic_data: bool = False`
  - [x] 3.3 Implement `validate_incident(data: dict) -> IncidentReport` raising `ValueError` with field name on missing/invalid input
  - [x] 3.4 Define `THRESHOLDS` dict for `blocked_drain`, `bridge_vibration`, `water_leak` (empty dicts for `streetlight_failure`, `pothole`)
  - [x] 3.5 Implement `check_thresholds(report: IncidentReport) -> tuple[bool, list[str]]` — pure code, no model calls
  - [x] 3.6 Implement `build_state_string(report: IncidentReport, sensor_summary: str) -> str` — pre-computes summary from sensor readings before passing to Decider
  - [x] 3.7 Implement `@tool triage_incident(incident_json: str) -> dict` following the flow: validate → thresholds → (skip Decider if override) → PolicyCheck.check → return triage result dict
  - [x] 3.8 Implement `@tool generate_explanation(triage_result_json: str) -> dict` calling `decider_kit.llm.ask()` with prompt built from Verdict fields; return placeholder on LLM failure
  - [x] 3.9 Implement `@tool dispatch_action(incident_id: str, approved_by: str, approved_at: str, action: str) -> dict` — validate `approved_by` non-empty; return error if missing; never auto-call
  - [x] 3.10 Export `TOOLS = [triage_incident, generate_explanation, dispatch_action]`
  - Verification: `python -c "from agent.tools import TOOLS, THRESHOLDS, check_thresholds, validate_incident; print('tools ok')"` passes without error

- [x] 4. Write `agent/agent.py` — `HackathonAgent` with Policy pattern wired in
  - [x] 4.1 Set `NAME = "CivicPulse — City Infrastructure Triage"`
  - [x] 4.2 Write `SYSTEM_PROMPT` instructing the agent to call `triage_incident` then `generate_explanation`, and never auto-call `dispatch_action`
  - [x] 4.3 Import `IncidentTriagePolicy` from `agent/decision.py` and `TOOLS` from `agent/tools.py`
  - [x] 4.4 Implement `HackathonAgent.__init__` accepting `decider`, `model_id`, `eager`; initialise `IncidentTriagePolicy` with the decider
  - [x] 4.5 Implement `_session(session_id)` creating a Strands `Agent` with `TOOLS`, `SYSTEM_PROMPT`, and `IncidentTriagePolicy` as an `InterventionHandler`; store per session
  - [x] 4.6 Implement `chat(message, session_id="default", images=None) -> dict` returning `reply`, `decisions` (from `guard.log`), `model_id`, `latency_ms`, `agent`, `eager`
  - [x] 4.7 Ensure `decisions` list items include: `pattern`, `incident_id`, `category`, `p_violation`, `severity`, `confident`, `verdict`, `decider_ms`
  - Verification: `python -c "from agent.agent import NAME, HackathonAgent; from tests.fakes import FakeDecider; a = HackathonAgent(decider=FakeDecider(), model_id='test'); print(NAME)"` passes

- [x] 5. Write `agent/AGENT.md` — use-case description
  - [x] 5.1 Replace kit example content with CivicPulse pitch, audience, and features
  - [x] 5.2 Document the decision handed to Strands Decider 2B: incident triage policy, `#policy`
  - [x] 5.3 Document local vs production configuration table (LLM_PROVIDER, DECIDER_MODE, credentials)
  - [x] 5.4 Add note: "Built with the AWS User Group Madurai Strands Decider hackathon kit"
  - Verification: file exists, contains `#policy`, contains `LLM_PROVIDER`, does not contain `IS_KIT_EXAMPLE`

- [x] 6. Write `agent/features.yaml` — AgentCore deployment features
  - [x] 6.1 Set `memory.enabled: false` and `gateway.enabled: false` (CivicPulse prototype needs neither)
  - [x] 6.2 Keep all other fields (name, strategies, targets) as valid YAML per the kit schema
  - Verification: YAML parses without error; `memory.enabled` and `gateway.enabled` are both `false`

- [x] 7. Write property-based tests in `tests/test_civic_pulse.py`
  - [x] 7.1 Test round-trip invariant: `IncidentReport` → dict → `validate_incident` → equal fields
  - [x] 7.2 Test monotonicity invariant: for all incident types with thresholds, any reading ≥ threshold always yields `safety_override=True`
  - [x] 7.3 Test safety override always maps to `priority=immediate_dispatch` in `triage_incident` (using `FakeDecider`)
  - [x] 7.4 Test closed-set invariant: Verdict action always in `{"allow", "review", "block"}`
  - [x] 7.5 Test closed-set invariant: triage result `priority` always in `PRIORITY_VALUES`
  - [x] 7.6 Test human-gate invariant: `dispatch_action` with empty `approved_by` always returns error dict and never dispatches
  - [x] 7.7 Test error resilience: missing required field → structured error dict with `error_type` and `error_message`, no exception raised
  - [x] 7.8 Test Decider not called when `safety_override=True` (check `FakeDecider.calls` is empty)
  - [x] 7.9 Test `confident=False` from Decider forces `verdict_action=review`
  - [x] 7.10 Test `generate_explanation` returns non-empty string and does not raise on LLM error (mock `decider_kit.llm.ask` to raise)
  - Verification: `.\kit.cmd test` passes with all new tests green, no AWS credentials required

- [x] 8. Write `agent/eval_cases.jsonl` — 20+ labelled eval cases
  - [x] 8.1 Write 5 `tune` cases: blocked drain above threshold, bridge vibration below threshold, streetlight outage no sensor, water leak above threshold, pothole citizen report
  - [x] 8.2 Write 5 `test` cases: same scenario types as above but different values and descriptions
  - [x] 8.3 Write 4 ambiguous/low-confidence `tune` cases: contradictory sensor+description, incomplete description, unusual combination
  - [x] 8.4 Write 4 ambiguous/low-confidence `test` cases
  - [x] 8.5 Write 2 `invalid` cases (missing field, unknown type) in both splits
  - [x] 8.6 Verify each case `state` string matches the format passed to `PolicyCheck.check()` at runtime
  - [x] 8.7 Verify each case `expected` dict keys are a subset of `QUESTIONS` (required by `test_eval_cases_match_questions`)
  - Verification: `python -c "import json; cases=[json.loads(l) for l in open('agent/eval_cases.jsonl') if l.strip()]; assert len(cases)>=20; print(len(cases),'cases ok')"` passes

- [x] 9. Write `agent/demo_script.yaml` — four mandatory demo scenarios
  - [x] 9.1 Replace kit example turns with CivicPulse `title`, `pitch`, `features`, `intro`, `intro_normal`, `outro`
  - [x] 9.2 Write Scenario A turn: blocked drain, water_level_m=0.92 (above 0.9 threshold); narration states data is synthetic
  - [x] 9.3 Write Scenario B turn: bridge vibration, vibration_mm_s=4.2 (below 5.0 threshold); narration states data is synthetic
  - [x] 9.4 Write Scenario C turn: streetlight outage, no sensor data; narration states data is synthetic
  - [x] 9.5 Write Scenario D turn: invalid report with missing incident_type; narration states data is synthetic
  - [x] 9.6 Ensure each `say` field contains a JSON incident report in the format `triage_incident` expects
  - Verification: YAML parses without error; 4 turns present; each `narrate` field contains the word "synthetic"

- [x] 10. Run the kit contract tests and fix any failures
  - [x] 10.1 Run `.\kit.cmd test` and confirm `test_decision_file_is_labelled` passes
  - [x] 10.2 Run `.\kit.cmd test` and confirm `test_eval_cases_match_questions` passes
  - [x] 10.3 Run `.\kit.cmd test` and confirm `test_agent_interface` passes
  - [x] 10.4 Fix any `IS_KIT_EXAMPLE` reference found by the submission check
  - [x] 10.5 Confirm no boto3 / direct Bedrock API calls exist in `agent/` (grep check)
  - Verification: `.\kit.cmd test` exits 0; all tests pass

- [x] 11. Smoke-test the local end-to-end workflow
  - [x] 11.1 Confirm `LLM_PROVIDER=local` and `DECIDER_MODE=local` (or mocked) are set in `.env` or test env
  - [x] 11.2 Run `.\kit.cmd run` and confirm the agent responds to a sample incident JSON without error
  - [x] 11.3 Verify the response dict contains `reply`, `decisions`, `model_id`, `latency_ms`
  - [x] 11.4 Verify `decisions` contains at least one item with `pattern="#policy"` (or `safety_override` path returns zero decisions as expected)
  - Verification: `.\kit.cmd run` exits 0 with a valid JSON response; no AWS credentials needed

- [x] 12. Prepare for AWS Bedrock and AgentCore deployment (requires AWS credentials — do last)
  - [ ] 12.1 Configure AWS credentials via `.\kit.cmd aws-login` (user types keys in terminal, not in chat)
  - [ ] 12.2 Run `.\kit.cmd setup --yes --llm bedrock` to verify Bedrock connectivity
  - [ ] 12.3 Run `.\kit.cmd bench --split tune` to generate tune-split accuracy numbers
  - [ ] 12.4 Run `.\kit.cmd bench --split test` to generate held-out test accuracy numbers; save to `submission/bench/`
  - [ ] 12.5 Run `.\kit.cmd deploy` to deploy to Amazon Bedrock AgentCore Runtime
  - [ ] 12.6 Run `.\kit.cmd invoke "triage this: {incident_type: bridge_vibration, ...}"` against the deployed agent to confirm it works
  - Verification: `.\kit.cmd check` passes all submission requirements; `submission/bench/results.json` exists
