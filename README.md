# autonetops_infrahub — Intent-Based Networking Lab

InfraHub-backed intent pipeline: a follow-along lab where network intent is
declared per **realm**, achieved via **policies**, made enforceable by
**contracts** (compiled to configs, expectations and observability) and
**invariants** (compiled to merge-blocking checks).

> **Intent is stable; renderers change. Expectations are the proof.**

## The model

```
IntentRealm            routing | reachability | security | observability |
                       reliability | compliance | performance
  └─ IntentIntent      a declared outcome: tenant, priority, statement
       └─ IntentPolicy how it is realized (design_time / runtime / full)
            ├─ contracts   IntentContract subtypes: Routing, Reachability,
            │              Security, Observability, Reliability
            └─ invariants  IntentInvariant: no_leak, prefix_authorization,
                           redundancy, capability_present, ... (blocking|warning)
```

Contracts feed the compilers; invariants feed the merge gates. Supporting
kinds: `IntentTenant` (who), `IntentZone` (where), `IntentCapability`
(what a platform can do).

## Layout

```
schemas/intent.yml  the whole intent model (realm/intent/policy/contract/invariant)
.infrahub.yml       pipeline wiring (queries, checks, transforms, artifacts)
queries/            GraphQL read-contracts for checks and compilers
checks/             merge-gating invariants (no-leak, prefix-auth, redundancy,
                    OOB reachability, observability capability)
transforms/         compilers: device configs (EOS / SR Linux / IOS-XE / FRR,
                    plus Junos), ContractExpectations, telegraf inputs,
                    Prometheus rules, Grafana dashboards
scripts/            bootstrap data, artifact fetcher, config pusher,
                    CLI-scrape telemetry collector (for gNMI-less platforms)
clab/               lab topology (mirrors the SoT 1:1, incl. the OOB plane)
monitoring/         static telegraf/Prometheus/Grafana plumbing; compiled
                    fragments land in telegraf.d/, rules/, dashboards/
docs/workshop.md    the full walkthrough (phases 0-7 + drift exercises)
docs/workshop2.md   IBN theory & terminology (realm/intent/policy/contract/
                    invariant/expectation, the control loop, design laws),
                    day-2 operations: change the SoT on a branch, let the
                    orchestrator plan/schedule/dispatch it (change windows
                    live in the SoT: schemas/operations.yml), and the staged
                    adoption blueprint for automation + AI at scale
```

Delivery beyond the hand-run scripts lives in the companion
[`autonetops_ibn`](../autonetops_ibn) project: planner → staged Plan →
`ibn-orchestrate` (timeline vs SoT change windows, dispatch decision,
validator-gated stages) → executor drivers (snapshot / dry_run / apply /
rollback, with evidence per verb). `ibnctl` remains the manual,
break-glass runner. See [docs/workshop2.md](docs/workshop2.md).

## Quick start

See [docs/workshop.md](docs/workshop.md). Short version:

```bash
infrahubctl schema load ../schema-library/base \
    ../schema-library/extensions/{vrf,cable,location_minimal,routing_bgp}
infrahubctl schema load schemas/intent.yml
python scripts/bootstrap.py
cd clab && sudo containerlab deploy -t lab.clab.yml
python ../scripts/fetch_artifacts.py && python ../scripts/deploy_configs.py
cd ../monitoring && docker compose up -d --build
```

## The one-sentence architecture

Intent (realm → intent → policy → contracts + invariants, in InfraHub) →
compiled artifacts (configs + expectations + collectors + alerts +
dashboards) → planned, canary-first delivery (autonetops_ibn) → containerlab
network → telemetry evidence → alerts that fire exactly when an expectation
stops holding.
