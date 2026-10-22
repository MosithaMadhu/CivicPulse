# CivicPulse — City Infrastructure Triage

## Pitch

CivicPulse is a city infrastructure triage agent. It classifies incoming incident reports
(blocked drains, bridge vibrations, streetlight failures, potholes, water leaks) into one of
five priority levels and explains what action the maintenance team should take — without
making that decision purely inside a black-box LLM.

## Who it is for

- City maintenance operations centres that receive hundreds of incident reports per day
- On-call engineers who need a fast, auditable priority decision before dispatching crews
- Councils that must justify triage decisions under public scrutiny

## Features

- Validates every incident report (type, required fields) before acting
- Deterministic threshold checks for sensor-equipped incident types (blocked drain, bridge
  vibration, water leak) — no model call needed for obvious emergencies
- Classifies all other incidents with Strands Decider 2B against a written triage policy
- Generates a plain-language explanation and maintenance recommendation via the LLM
- Human approval required for any dispatch action — never dispatches automatically
- Live decision panel in the demo UI shows Decider probabilities and confidence per call
- Deployed on Amazon Bedrock AgentCore Runtime

## The decision handed to Strands Decider 2B: incident triage policy (`#policy`)

Before any non-safety-override incident is dispatched, Strands Decider 2B reads the incident
state (type, location, description, sensor readings) and answers three typed questions:

| Question | Type | What it decides |
|---|---|---|
| `priority_category` | choice | Which of five priority levels best fits this incident? |
| `confidence_adequate` | noul | Is the evidence sufficient to act without human review? |
| `requires_specialist` | noul | Does this need a specialist crew (engineer, utilities)? |

The five priority categories are:
- `immediate_dispatch` — risk to life, structural failure, or active flooding
- `same_day` — significant disruption or escalation risk
- `scheduled` — routine defect requiring planned maintenance
- `monitor` — minor issue, no immediate action needed
- `invalid` — incomplete, unrecognisable, or test report

When `confidence_adequate` is false, the verdict is forced to `review` and a human must
inspect the incident before any crew is dispatched.

## Local vs production configuration

| Component | Local (`LLM_PROVIDER=local`, `DECIDER_MODE=local`) | Production (`LLM_PROVIDER=bedrock`) |
|---|---|---|
| LLM | Ollama (`qwen3.5:4b` default, set `LLM_MODEL_ID`) | Amazon Bedrock (`us.amazon.nova-pro-v1:0` default) |
| Decider | Local server at `DECIDER_URL` | Hosted API or local, via `decider_kit.Decider` |
| AWS credentials | None required | AWS profile `hackathon` |
| `model_id` in response | Ollama model id | Bedrock model id |

Set `LLM_PROVIDER`, `DECIDER_MODE`, and `DECIDER_URL` in `.env` (written by `./kit setup`).

## Running locally

```bash
# Start the decider server in one terminal
./kit serve

# Start the agent demo in another
./kit demo
```

Or for a single scripted conversation:

```bash
./kit run
```

## Built with the AWS User Group Madurai Strands Decider hackathon kit
