# Reporting Contract

Step 4 defines two reporting sources and keeps them separate.

## Source of Truth

Deterministic accounting source:

- Use reserve/settle-derived data for billed totals.
- This source owns total spend, average spend per run, budget-used percentages,
  spend-over-time totals, and other enforcement-facing or billing-facing numbers.

Attributed events source:

- Use canonical `llm.usage` events for spend breakdowns.
- This source owns spend by scope, spend by tool, run-internal breakdowns,
  root-only analysis, and attributed activity timelines.

Mixed responses:

- Responses that need both totals and breakdowns must return them separately.
- Do not compute billed totals by summing events.

## Canonical Envelope

Canonical events are emitted in `snake_case` and promote first-class reporting
dimensions to top-level fields:

- identity: `id`, `ts`, `ingested_at`, `tenant_id`, `agent_id`, `user_id`,
  `run_id`, `trace_id`, `span_id`
- event identity: `category`, `name`, `version`, `severity`, `outcome`
- usage: `provider`, `model`, `usd_micros`, `input_tokens`,
  `cached_input_tokens`, `output_tokens`
- attribution: `scope_id`, `scope_name`, `scope_kind`, `parent_scope_id`,
  `root_scope_id`, `tool_name`
- optional debug detail: `payload`, `evidence`, `meta`

`event_type` is not part of the canonical envelope. `category + name` is the
canonical event identity.

## Attributed Spend Event

Every successful real provider call emits exactly one canonical attributed spend
event:

- `category = "llm"`
- `name = "usage"`

This row is the only event row eligible for spend-by-scope/tool aggregations.
Generic runtime events such as `tool.invoke`, `run.start`, `run.end`, or
`guard.blocked` are not spend rows even if they carry other metadata.

## Root-Only and Unattributed

- `root_only` means a canonical `llm.usage` event with `scope_kind = "root"`
  and no `scope_name`.
- `unattributed` is a synthetic reconciliation bucket:
  `deterministic_total - summed_attributed_llm_usage`.

`unattributed` is not a raw event bucket and must not be added on top of the
deterministic total.
