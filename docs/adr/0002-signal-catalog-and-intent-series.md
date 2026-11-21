# ADR 0002 — Signal catalog and intent series: joining intent to telemetry

- **Status:** Accepted (step 1 of the expectations-as-IR plan)
- **Date:** 2026-11-21
- **Scope:** `transforms/` (signal_catalog, contract_expectations, intent_series,
  prometheus_rules, telemetry_collector, grafana_dashboard), `.infrahub.yml`,
  `scripts/fetch_artifacts.py`

## Context

Three compilers each re-read the intent graph and independently decided what
a signal means: `telemetry_collector` (gNMI paths), `prometheus_rules`
(metric names + regex-parsed condition), `contract_expectations` (what the
gate validator checks). The metric names and the `contract` label were an
undocumented agreement between those files and the telegraf starlark
processor. Two concrete failures followed:

1. Collectors stamped `contract=<contracts[0]>` on every subscription of a
   device, so a PE serving two contracts (PE-EMEA-02: `custc-ce-to-pe` and
   `inet-peering-emea`) reported every neighbor under the first contract.
2. Staleness was `absent(metric{contract=X})` — one missing session among N
   was invisible.

## Decision

1. **Signal catalog** (`transforms/signal_catalog.py`) is the single binding
   from a signal id to: canonical metric, join labels, scope, threshold
   source (contract field), per-platform gNMI paths, CLI-scrape support and
   the gate validator's `get_facts()` path. Unknown signal ids fail the
   artifact.
2. **Collectors emit raw facts** tagged only with join keys
   (`device`, `neighbor`). They never carry intent labels.
3. **Intent series**: a new `intent-series` artifact (per routing contract)
   publishes what the intent expects as Prometheus recording rules:
   `intent_session_info{contract,tenant,intent,role,device,neighbor,...} 1`
   and `intent_threshold{contract,term,afi,bound} <value>`.
4. **Alert rules and dashboards are joins**:
   `metric * on(device,neighbor) group_left(contract,tenant) intent_session_info{...}`,
   bounded by `> on(contract) group_left() intent_threshold{...}`.
   Staleness is per expected session:
   `intent_session_info unless on(device,neighbor) metric`, plus an
   `expectations-missing` guard on the series themselves.
5. **Expectations are the IR**: `contract_expectations.build_expectations()`
   is the only reader of the routing-contract graph for observability;
   `intent_series` projects from it, and each `telemetry.required` entry
   carries its catalog `evidence` binding (metric, joinOn, factsPath,
   threshold).

## Consequences

- Thresholds are data: changing `max_prefixes_ipv4` recompiles a series,
  not rules or collectors.
- Contract selection in dashboards uses the `role` label, not contract-name
  regexes.
- Delivery must ship `intent-series` alongside `prometheus-rules`
  (same `rule_files` glob); otherwise `<contract>-expectations-missing` fires.
- Verified: promtool `check rules` and a promtool unit test of the joins
  (two contracts on one PE, threshold breach, one unobserved session).
  Not yet verified against live gNMI tag names on cEOS.

## Owed (next steps)

- Typed predicates replacing `ObservabilitySignal.condition` text (step 2).
- Gate validator reads `evidence.factsPath` instead of hard-coding sessions (step 3).
- `policy_export_violation` is a count proxy; real leak evidence needs the
  advertised prefix set (BMP / adj-rib-out) diffed against `allowed_prefixes`.
