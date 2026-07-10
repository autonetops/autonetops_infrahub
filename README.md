# autonetops_infrahub — Intent-Based Networking Lab

InfraHub-backed intent pipeline: a follow-along lab where network intent is
modeled as typed contracts, validated by merge-blocking invariants, and
compiled into device configurations, machine-readable expectations and a
full observability stack.

> **Intent is stable; renderers change. Expectations are the proof.**

## Layout

```
.infrahub.yml       pipeline wiring (queries, checks, transforms, artifacts)
queries/            GraphQL read-contracts for checks and compilers
checks/             merge-gating invariants (no-leak, prefix-auth, redundancy,
                    OOB reachability, observability capability)
transforms/         compilers: device configs (EOS / SR Linux / IOS-XE / FRR,
                    plus Junos), ContractExpectations, telegraf inputs,
                    Prometheus rules, Grafana dashboards
scripts/            bootstrap data, artifact fetcher, config pusher,
                    CLI-scrape telemetry collector (for gNMI-less platforms)
containerlab/       lab topology (mirrors the SoT 1:1, incl. the OOB plane)
monitoring/         static telegraf/Prometheus/Grafana plumbing; compiled
                    fragments land in telegraf.d/, rules/, dashboards/
docs/workshop.md    the full walkthrough (phases 0-7 + drift exercises)
```

Schemas live in the companion repo:
`schema-library/experimental/intent` (plus `base`, `vrf`, `cable`,
`location_minimal`, `routing_bgp`).

## Quick start

See [docs/workshop.md](docs/workshop.md). Short version:

```bash
infrahubctl schema load ../schema-library/base \
    ../schema-library/extensions/{vrf,cable,location_minimal,routing_bgp} \
    ../schema-library/experimental/intent
python scripts/bootstrap.py
cd containerlab && sudo containerlab deploy -t topology.clab.yml
python ../scripts/fetch_artifacts.py && python ../scripts/deploy_configs.py
cd ../monitoring && docker compose up -d --build
```

## The one-sentence architecture

Intent (typed, invariant-checked, in InfraHub) → compiled artifacts
(configs + expectations + collectors + alerts + dashboards) → containerlab
network → telemetry evidence → alerts that fire exactly when an expectation
stops holding.
