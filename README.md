# magicpin AI Challenge — Submission

## Approach

A deterministic, rule-based composer — **no LLM call in the hot path**. `compose(category, merchant, trigger, customer=None)` in `composer.py` dispatches on `trigger["kind"]` to one of 26 hand-written handlers (covering every kind present in the base dataset plus the ones `generate_dataset.py` adds: `appointment_tomorrow`, `trial_followup`, `customer_lapsed_soft`), each of which builds a WhatsApp message purely out of fields actually present in the four contexts — never invented data.

Every handler is built around the brief's compulsion levers and anti-patterns:

- **Specificity**: every template pulls a real number, date, source citation, or offer title out of the trigger/merchant/category payloads (JIDA page numbers, % deltas, ₹ prices, distances, batch numbers) rather than saying "increase your sales."
- **Category fit**: tone is read from `category.voice` — dentists/pharmacies get a clinical-peer register, gyms a coach register, salons/restaurants a warm-operator register — and Hindi-English code-mix (`mix()` helper) is applied only when the merchant's `languages`/category `code_mix` or the customer's `language_pref` calls for it.
- **Merchant/customer fit**: owner first name, locality, active offers, signals (e.g. `high_risk_adult_cohort`), and customer state/preferences are woven in directly.
- **Trigger relevance**: the "why now" is always named explicitly (the digest source, the % dip, the days-to-renewal, the recall due-date).
- **Engagement compulsion**: loss aversion (renewal/perf-dip), social proof (peer_stats), effort externalization ("I'll draft it — just say go"), curiosity ("want the full list?"), and always a single primary CTA — binary for action triggers, open-ended for pure info, never multi-choice.
- **Anti-patterns avoided**: no generic "% off" (offer catalog is always service+price), no re-introductions, one CTA per message, no fabricated citations.

Conversation handling (`conversation.py`, used by `POST /v1/reply`) is a small pattern-based state machine, not an LLM call, targeting the three explicit open challenges:

1. **Auto-reply detection** — a regex bank of canned-reply phrases (English + Hindi). First hit gets one soft human-check; a second hit for the *same merchant* (tracked globally, since the judge's replay uses a fresh `conversation_id` every turn) ends the conversation instead of looping forever.
2. **Intent handoff** — commitment phrases ("let's do it", "ok", "confirm", "haan kar do", ...) immediately switch the reply to action-mode language ("sending the link now") instead of re-qualifying.
3. **Graceful exit** — hostile language or explicit not-interested/unsubscribe signals end the conversation on the first turn, politely, without escalation. Off-topic-but-not-hostile questions get a polite redirect rather than a refusal.

## Why rule-based instead of an LLM call

The testing brief requires: deterministic output, <30s per call, and survival of a 60-minute live test window with rate limits and healthz checks. A rule-based composer satisfies all three by construction — no risk of an upstream LLM provider timing out, rate-limiting, or drifting non-deterministically mid-test. It also means every claim in every message is traceably grounded in the pushed context, which directly avoids the judge's harshest penalty (fabrication, -2) and the "don't fabricate" constraint in §5.6 of the brief.

The tradeoff: rule-based copy has a ceiling on *variety* an LLM wouldn't have, and new trigger `kind`s not in `HANDLERS` fall back to a generic-but-still-grounded template (`h_generic`) rather than getting bespoke framing. Swapping in an LLM call behind the same `compose()` signature (temperature=0, with the same anti-fabrication guardrail applied as a post-check) would be the natural v2 — the four-context inputs and output contract don't change.

## Files

| File | Purpose |
|---|---|
| `bot.py` | FastAPI app — the 5 required endpoints (`/v1/context`, `/v1/tick`, `/v1/reply`, `/v1/healthz`, `/v1/metadata`) plus optional `/v1/teardown` |
| `composer.py` | Stateless `compose()` — the core deliverable |
| `conversation.py` | Stateful reply handling: auto-reply/hostile/intent classification + graceful exit |
| `generate_dataset.py` | Provided expander — seeds → full 255-context dataset + 30 test pairs (unmodified) |
| `generate_submission.py` | Produces `submission.jsonl` by calling `compose()` directly, offline, for the 30 canonical test pairs |
| `submission.jsonl` | The required 30-line output |
| `smoke_test.py` | Dev-only local harness: pushes the full dataset, exercises tick/dedup/reply scenarios (not part of the official submission surface, but useful before running the official `judge_simulator.py`) |
| `Dockerfile` | One-command deploy to any container host |

## Running it

```bash
pip install -r requirements.txt
uvicorn bot:app --host 0.0.0.0 --port 8080
```

Then, to self-test before pointing the judge at it:

```bash
python generate_dataset.py --seed-dir dataset_seed --out dataset   # build the dataset once
python smoke_test.py                                               # local sanity checks
python generate_submission.py                                      # (re)build submission.jsonl
```

Or with the official simulator (requires an LLM API key for the scoring side only — the bot itself needs none):

```bash
export BOT_URL=http://localhost:8080
python judge_simulator.py
```

## Tradeoffs / what more context would help

- **No persistence beyond the process.** All context and conversation state is in-memory, per the testing brief ("in-memory is fine; no restarts during test"). A real deployment would want Redis, as production Vera does.
- **Trigger-kind coverage is enumerated, not learned.** 26 explicit handlers cover every kind in the base + generated dataset; anything genuinely novel gets the generic fallback. An LLM-backed composer (or a retrieval step over `digest`/`patient_content_library`) would generalize better to unseen kinds — the biggest single upgrade path.
- **Language mixing is template-level, not generative.** True per-message Hindi-English code-switching (matching the fluency of the brief's Pattern A example) would benefit from a small fine-tuned or prompted pass layered on top of the deterministic skeleton, with the skeleton's facts as hard constraints so it can't drift into fabrication.
- **Multi-turn cadence planning (open challenge #3)** is currently just "don't send more than once per suppression key, cap at 5 turns per conversation" — a smarter session-level planner (e.g. don't send two `urgency<=2` merchant-facing nudges within the same 24h window) would need an explicit per-merchant send-history model, which the `MerchantContext.conversation_history` field already has the shape for.
