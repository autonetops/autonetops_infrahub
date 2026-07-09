# Intent-Based Networking Workshop

Follow-along lab for the article *"Modeling Intent for Real Networks"*.
InfraHub is the intent source of truth; the schema-library `experimental/intent`
extension is the vendor-agnostic contract layer; this repo is the pipeline:
checks gate intent, transforms compile it, containerlab runs it, and
telegraf → Prometheus → Grafana prove it.

**The design law:** intent is stable; renderers change. Expectations are the proof.

## Architecture recap

```
 InfraHub (intent SoT)
   branch -> edit intent -> Proposed Change
     |            checks: no-leak, prefix-auth, redundancy,
     |                    OOB reachability, observability capability
     v  merge = compile
   artifacts: device configs | expectations | telegraf inputs
              prometheus rules | grafana dashboards
     v  fetch_artifacts.py + deploy_configs.py   (execution stand-in)
   containerlab: cisco IOL PE + cEOS PE + SR Linux RR + FRR CEs/peer + OOB bridge
     v  evidence
   telegraf (gNMI or CLI-scrape, capability-driven) -> Prometheus -> Grafana
```

The execution/orchestration layer is deliberately out of scope. Where it
would sit, this lab uses two scripts you run by hand — the *behavior* a real
runner would have is documented in each script's docstring.

## Phase 0 — Foundations

Load schemas in dependency order (schema-library checked out next to this repo):

```bash
infrahubctl schema load ../schema-library/base
infrahubctl schema load ../schema-library/extensions/vrf \
                        ../schema-library/extensions/cable \
                        ../schema-library/extensions/location_minimal \
                        ../schema-library/extensions/routing_bgp
infrahubctl schema load ../schema-library/experimental/intent
```

Add this repo to InfraHub as a Git repository (Integrations → Repository) so
`.infrahub.yml` registers the queries, checks, transforms and artifact
definitions.

## Phase 1 — Look at the intent model

Browse the schema in the UI. The things to notice:

- **Zones, capabilities, roles, severities are controlled vocabularies.**
  There is no field where "internet-peers " (trailing space) can silently
  fracture the model.
- **`IntentRoutingContract` never names a mechanism.** No route-map, no
  policy-options, no vendor block. Try to find a place to put a Junos knob —
  there isn't one. That's principle A.
- **`DcimPlatform.capabilities`** is where "this Cisco has no gNMI" lives as
  data. Everything downstream branches on it.

## Phase 2 — Seed the customer-c scenario

```bash
export INFRAHUB_ADDRESS=http://localhost:8000 INFRAHUB_API_TOKEN=...
python scripts/bootstrap.py
```

This loads: 7 devices (2 PEs, 1 RR, 2 CEs, 1 internet peer, 1 OOB switch),
full cabling including the OOB plane, the CUSTC-PROD VRF and prefixes, the
tenant, three contracts (`custc-ce-to-pe`, `inet-peering-emea`,
`ibgp-core-rr`), invariants, reachability, security, reliability and four
observability signals — plus the groups the pipeline targets.

Do it on a branch and open a Proposed Change to watch every check run green
before merging.

## Phase 3 — Invariants as merge gates

Each check is a semantic statement, not a syntax check:

| Check | Invariant |
|---|---|
| `contract_no_leak` | a no-leak tenant may not appear in `export_tenants` of any contract toward a denied zone |
| `contract_prefix_authorization` | `allowed_prefixes` ⊆ tenant segments |
| `tenant_redundancy` | ≥ N distinct PEs, distinct failure domains |
| `oob_reachability` | every device has a management interface cabled to an `oob_switches` member |
| `observability_capability` | every required signal is collectable on every attachment PE |

**Exercise 3a (the leak that never ships):** on a branch, edit
`inet-peering-emea` and add `customer-c` to `export_tenants`. Open a Proposed
Change. `contract_no_leak` fails with a message naming the tenant, the
contract and the denied zone. The bad PR never merges.

**Exercise 3b (unauthorized space):** add `8.8.8.0/24` to
`custc-ce-to-pe.allowed_prefixes`. `contract_prefix_authorization` blocks it —
the tenant doesn't own that space.

**Exercise 3c (quiet degradation):** remove `pe-emea-02` from the contract's
`pe_devices`. `tenant_redundancy` blocks the merge: 1 PE < required 2.

**Exercise 3d (unobservable = unmergeable):** remove the `gnmi` and `ssh_cli`
capabilities from the `cisco_iosxe` platform. `observability_capability`
fails: the required signals can no longer be collected from `pe-emea-01`.

## Phase 4 — Compilation: one intent, three vendors

Generate/inspect artifacts (UI → Artifacts, or a Proposed Change's artifact
diff). For the *same* `custc-ce-to-pe` contract:

- `pe-emea-01` gets IOS-XE: `vrf definition`, `route-map CUSTC-CE-TO-PE-IN`,
  `maximum-prefix 200`
- `pe-emea-02` gets EOS: `vrf instance`, `route-map ... in`, `maximum-routes`,
  plus — from the *peering* contract — `route-map INET-PEERING-EMEA-OUT deny`
  matching customer-c's community. **That deny clause is the no-leak invariant
  materialized.**
- `core-rr-01` gets SR Linux set-commands: vpn-ipv4 RR with the PEs as clients,
  derived from the `ibgp-core-rr` contract + modeled loopbacks.
- CEs/peer get FRR: they advertise exactly `allowed_prefixes`, nothing else.

Note what you did NOT write: sessions. They're derived from contract +
cabling. Change a cable in the SoT and the sessions everywhere recompile.

Now change the intent (e.g. `local_preference: 120 → 200`) on a branch and
open a Proposed Change: the artifact diff shows the exact config impact on
both vendors *before* merge.

## Phase 5 — Expectations are first-class artifacts

Open the `contract-expectations` artifact for `custc-ce-to-pe`. It encodes:

- sessions that must be `established` (with the *collector* for each device:
  `gnmi` for the cEOS PE, `cli_scrape` for the Cisco — capability-driven)
- route counts as **ranges** (`<= 200`), never exact values
- forbidden behavior (`export_prefixes_from_tenant(customer-c)` toward
  internet-peers/transit)
- telemetry freshness (2× collection interval — stale evidence is no evidence)

This file is what a validator consumes. It's versioned, diffable and merges
through the same Proposed Change flow as everything else.

## Phase 6 — Compiled observability

Inspect the `telegraf-inputs` artifacts:

- `pe-emea-02` / `core-rr-01`: `[[inputs.gnmi]]` subscriptions, one per
  signal, sample interval straight from `frequency_seconds`.
- `pe-emea-01` (Cisco, no gNMI): `[[inputs.exec]]` running
  `cli_bgp_telemetry.py` over SSH — same signals, different evidence pipeline.
  **The intent did not change; the platform's capabilities selected the
  collector.**

And `prometheus-rules` per contract: session-down (sev2, `for: 60s` from the
condition DSL), route-count > 200 (sev2), export-violation (sev1), plus a
staleness alert per signal.

And one Grafana dashboard per tenant, compiled — panels for session state,
route counts with the authorized max drawn as a threshold line, leak watch,
OOB reachability.

## Phase 7 — Bring the lab up and close the loop

```bash
cd containerlab && sudo containerlab deploy -t topology.clab.yml
python scripts/fetch_artifacts.py         # artifacts -> build/ + monitoring/
python scripts/deploy_configs.py          # push configs (execution stand-in)
cd monitoring && docker compose up -d --build
```

Grafana: http://localhost:3000 · Prometheus: http://localhost:9090.
Wait for sessions to establish, then the drift exercises:

**Drift 1 — the leak (sev1).** On `pe-emea-02`, remove the outbound guard:
```
configure
router bgp 65010
no neighbor 203.0.113.1 route-map INET-PEERING-EMEA-OUT out
```
Customer-c routes reach the internet peer; `bgp_session_prefixes_sent` on the
peering contract jumps past the authorized bound; the compiled
`inet-peering-emea-export-violation` alert fires. Intent declared it,
expectations encoded it, Prometheus proved it.

**Drift 2 — max-prefix (sev2).** On `ce-custc-01` (FRR), advertise 250 extra
prefixes inside 10.84.0.0/16. Watch `custc-ce-to-pe-route-count-exceeded`
fire, and the PE's `maximum-prefix` warning kick in.

**Drift 3 — redundancy holds.** Shut `ce-custc-01:eth1`. Reachability intent
survives (the tenant is dual-homed by *checked* invariant), session-down fires
sev2 for the failed attachment, and Grafana shows convergence within the
declared 30s budget.

**Drift 4 — evidence loss.** Stop the telegraf container. Every
`*-telemetry-stale` alert fires: if you can't observe it, you can't claim
compliance.

## Where a real execution layer plugs in

Everything below is *behavioral* description — nothing here is built, on
purpose:

1. A runner subscribes to InfraHub merge/artifact events.
2. On change, it fetches `device-configuration` artifacts and pushes them
   (gNMI Set / NETCONF / CLI — capability-driven, like everything else),
   ideally canary-first with automatic rollback on failed post-checks.
3. A validator loads `contract-expectations` artifacts and continuously
   evaluates them against the same Prometheus evidence, emitting
   `intent_compliance{contract=...} 0|1` — which becomes the rollout gate:
   a deploy isn't done when configs are pushed, it's done when expectations
   hold.
4. Safe drift classes (collector restart, session flap within budget)
   auto-remediate; invariant violations (a leak) page a human with the
   evidence bundle attached.

## Caveats (honest lab notes)

- Control-plane fidelity is the goal; **dataplane MPLS forwarding on cEOS
  containers is limited** — expectations validate BGP/policy facts, which is
  where the intent story lives anyway.
- If your SR Linux container build rejects the `vpn-ipv4` AF, switch the node
  type, or re-platform `core-rr-01` to FRR: one platform record + renderer
  selection change, zero intent changes. That *is* the lesson.
- gNMI field naming varies by OS version; the starlark normalizer in
  `monitoring/telegraf/telegraf.conf` covers the common cases and is the one
  place to extend.
