# Intent-Based Networking Workshop

Follow-along lab for the article *"Modeling Intent for Real Networks"*.
InfraHub is the intent source of truth; `schemas/intent.yml` (in this repo)
is the vendor-agnostic contract layer; this repo is the pipeline: checks
gate intent, transforms compile it, containerlab runs it, and
telegraf → Prometheus → Grafana prove it.

**The design law:** intent is stable; renderers change. Expectations are the proof.

## The intent model

Intents live in **realms** — domains of concern: `routing`, `reachability`,
`security`, `observability`, `reliability` (seeded and populated), plus
`compliance` and `performance` (seeded, awaiting their first intents). An
intent is achieved via **policies**, and each policy carries the two things
that make it enforceable:

```
IntentRealm                 the domain of concern
  └─ IntentIntent           the declared outcome (tenant, priority, statement)
       └─ IntentPolicy      how it is realized (enforcement: design_time/runtime/full)
            ├─ contracts    IntentContract subtypes - typed, machine-renderable
            │               promises: RoutingContract, ReachabilityContract,
            │               SecurityContract, ObservabilityContract,
            │               ReliabilityContract
            └─ invariants   IntentInvariant - conditions that must always hold;
                            compiled into merge-gating checks (severity:
                            blocking | warning)
```

Contracts are what the compilers consume (configs, expectations, collectors,
alerts, dashboards); invariants are what the checks enforce. Supporting
vocabulary — `IntentTenant`, `IntentZone`, `IntentCapability` — anchors the
who/where/what-can-the-box-do.

## Architecture recap

```
 InfraHub (intent SoT)
   branch -> edit realm/intent/policy/contract -> Proposed Change
     |            invariants gate the merge: no-leak, prefix-auth, redundancy,
     |                    OOB reachability, observability capability
     v  merge = compile
   artifacts: device configs | expectations | telegraf inputs
              prometheus rules | grafana dashboards
     v  delivery: autonetops_ibn (planner -> Plan -> ibnctl -> drivers)
        or fetch_artifacts.py + deploy_configs.py as the hand-run stand-in
   containerlab: 2x cEOS PE + cisco IOL RR + SR Linux CEs + FRR peer + OOB bridge
     v  evidence
   telegraf (gNMI or CLI-scrape, capability-driven) -> Prometheus -> Grafana
```

The full execution/orchestration layer lives in the companion
`autonetops_ibn` project: its planner diffs artifact listings into a staged,
canary-first Plan, and `ibnctl` walks the Plan through executor drivers
(snapshot → dry_run → apply → rollback-on-failure, evidence per verb). The
two scripts in this repo remain the minimal hand-run path.

## Phase 0 — Foundations

Load schemas in dependency order (schema-library checked out next to this repo):

```bash
infrahubctl schema load ../schema-library/base
infrahubctl schema load ../schema-library/extensions/vrf \
                        ../schema-library/extensions/cable \
                        ../schema-library/extensions/location_minimal \
                        ../schema-library/extensions/routing_bgp
infrahubctl schema load schemas/intent.yml
```

Add this repo to InfraHub as a Git repository (Integrations → Repository) so
`.infrahub.yml` registers the queries, checks, transforms and artifact
definitions.

## Phase 1 — Look at the intent model

Browse the schema in the UI. The things to notice:

- **The hierarchy is navigable end to end**: open the `routing` realm →
  `custc-l3vpn-connectivity` intent → `custc-edge-routing` policy → the
  `custc-ce-to-pe` contract and its three invariants. Every artifact and
  every merge gate traces back to a realm through this chain.
- **Zones, capabilities, roles, severities are controlled vocabularies.**
  There is no field where "internet-peers " (trailing space) can silently
  fracture the model.
- **`IntentRoutingContract` never names a mechanism.** No route-map, no
  policy-options, no vendor block. Try to find a place to put an SR Linux knob
  — there isn't one. That's principle A.
- **Invariants are declared objects, not tribal rules.** `custc-no-leak`
  (blocking), `custc-prefix-authorization` (blocking),
  `custc-reachability-preserved` (warning) — each names its type and severity
  on the policy it guards, and each maps onto a check.
- **`DcimPlatform.capabilities`** is where "this Cisco has no gNMI" lives as
  data. Everything downstream branches on it.

## Phase 2 — Seed the customer-c scenario

```bash
export INFRAHUB_ADDRESS=http://localhost:8000 INFRAHUB_API_TOKEN=...
python scripts/bootstrap.py
```

This loads: 7 devices (2 PEs, 1 RR, 2 CEs, 1 internet peer, 1 OOB switch),
full cabling including the OOB plane, the CUSTC-PROD VRF and prefixes, and
the intent hierarchy — 7 realms, 8 intents, 8 policies, 8 contracts across
five realms, 8 invariants and 4 observability signals — plus the groups the
pipeline targets (`network_devices`, `monitored_devices`, `oob_switches`,
`routing_contracts`, `observability_contracts`, `intent_tenants`).

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

**Exercise 3d (unobservable = unmergeable):** strip *every* telemetry
capability — `gnmi`, `ssh_cli` **and** `snmp` — from the `arista_eos`
platform. `observability_capability` fails: the required signals can no
longer be collected from either PE. Leave `snmp` on and the check still
passes; the invariant is "some telemetry capability", not "gNMI".

## Phase 4 — Compilation: one intent, four vendors

Generate/inspect artifacts (UI → Artifacts, or a Proposed Change's artifact
diff). For the *same* `custc-ce-to-pe` contract:

- `pe-emea-01` gets EOS: `vrf instance`, `route-map CUSTC-CE-TO-PE-IN`,
  `maximum-routes 200`
- `pe-emea-02` gets EOS too — but not the *same* config: it also carries the
  peering contract, so it alone grows `route-map INET-PEERING-EMEA-OUT deny`
  matching customer-c's community. **That deny clause is the no-leak invariant
  materialized.** Same renderer, same vendor, different intent attached.
- `core-rr-01` gets Cisco IOS (IOL): a vpnv4 route reflector with the PEs as
  `route-reflector-client` peers, derived from the `ibgp-core-rr` contract +
  modeled loopbacks, over an OSPF/LDP core underlay. The contract says
  `vpn-ipv4`; IOS spells it `address-family vpnv4` — vendor spelling stays in
  the renderer.
- CEs get SR Linux, the peer gets FRR: both advertise exactly
  `allowed_prefixes`, nothing else. On SR Linux that is a blackhole static per
  prefix released by a `routing-policy` export policy that also tags the
  contract communities; on FRR it is `network` + `ip route … blackhole`. Same
  intent, two grammars. Neither box has FRR's `remote-as external`, so the CE
  renderer reads the provider ASN off the modeled PE on the far end of the
  cable (SR Linux `peer-as`). Still derived, still no new intent field.

The IOS-XE renderer now knows three shapes — route reflector (vpnv4 + an
OSPF/LDP core), provider edge (VRFs, vpnv4 to the RR) and customer edge; SR
Linux knows two (customer edge, plus a dormant reflector body). Which shape a
device gets is read off the contract graph, not off a flag: a `pe_device` of a
`core` contract is the reflector, a device that is never a `pe_device` is a
customer endpoint, everything else is a PE. Re-platform a node onto any vendor
and the same rule picks the same shape — that is exactly the move that put the
reflector on IOL and the CEs on SR Linux.

Note what you did NOT write: sessions. They're derived from contract +
cabling. Change a cable in the SoT and the sessions everywhere recompile.

Now change the intent (e.g. `local_preference: 120 → 200`) on a branch and
open a Proposed Change: the artifact diff shows the exact config impact on
both vendors *before* merge.

## Phase 5 — Expectations are first-class artifacts

Open the `contract-expectations` artifact for `custc-ce-to-pe`. It encodes:

- sessions that must be `established`, each carrying the *collector* that will
  observe it (`observedVia: gnmi` for both cEOS PEs) — picked from the
  platform's capabilities, not written by hand. Re-platform a PE onto a box
  without gNMI and this field flips to `cli_scrape` on its own.
- route counts as **ranges** (`<= 200`), never exact values
- forbidden behavior (`export_prefixes_from_tenant(customer-c)` toward
  internet-peers/transit)
- telemetry freshness (2× collection interval — stale evidence is no evidence)

This file is what a validator consumes. It's versioned, diffable and merges
through the same Proposed Change flow as everything else.

## Phase 6 — Compiled observability

Inspect the `telegraf-inputs` artifacts:

- `pe-emea-01` / `pe-emea-02` (cEOS, gNMI-native): `[[inputs.gnmi]]`
  subscriptions on `:6030`, one per signal, sample interval straight from
  `frequency_seconds`.
- `core-rr-01` and the CEs: `[[inputs.ping]]` only. No contract-scoped signal
  points at them — signals attach to a contract's `pe_devices`, and none is
  scoped to `ibgp-core-rr` or the customer contract. Scope one at the SR Linux
  CEs and they compile a gNMI subscription on `:57400` with `tls_enable = true`
  (SR Linux serves gNMI behind a self-signed profile), against the
  `nokia_srlinux` native paths — not the openconfig paths the cEOS PEs use.
  Same signal, different paths, selected by platform.
- Nothing compiles to `[[inputs.exec]]` today, because no PE lacks gNMI. To see
  the CLI-scrape arm, scope a signal at `ibgp-core-rr`: the Cisco IOL reflector
  claims `ssh_cli` and no `gnmi`, so it compiles `cli_bgp_telemetry.py` over
  SSH instead. **The intent would not change; the platform's capabilities
  select the collector.**

And `prometheus-rules` per contract: session-down (sev2, `for: 60s` from the
condition DSL), route-count > 200 (sev2), export-violation (sev1), plus a
staleness alert per signal.

And one Grafana dashboard per tenant, compiled — panels for session state,
route counts with the authorized max drawn as a threshold line, leak watch,
OOB reachability.

## Phase 7 — Bring the lab up and close the loop

```bash
cd clab && sudo containerlab deploy -t lab.clab.yml
python scripts/fetch_artifacts.py         # artifacts -> build/ + monitoring/
python scripts/deploy_configs.py          # push configs (execution stand-in)
cd monitoring && docker compose up -d --build
```

Delivery works over the network (SSH, admin/admin) — the lab host and the
control host need not be the same machine. One address-planning rule learned
the hard way: **modeled underlay space must never overlap the management
path**. The core links live in `10.100.0.0/24` because a control host on
`10.0.0.0/24` would otherwise lose SSH to a PE the moment its core interface
comes up (the return path flips into the data plane).

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

**Drift 2 — max-prefix (sev2).** On `ce-custc-01` (SR Linux), advertise 250
extra prefixes inside 10.84.0.0/16. Watch `custc-ce-to-pe-route-count-exceeded`
fire, and the PE's `maximum-routes` warning kick in.

**Drift 3 — redundancy holds.** Shut `ce-custc-01:ethernet-1/1`. Reachability intent
survives (the tenant is dual-homed by *checked* invariant), session-down fires
sev2 for the failed attachment, and Grafana shows convergence within the
declared 30s budget.

**Drift 4 — evidence loss.** Stop the telegraf container. Every
`*-telemetry-stale` alert fires: if you can't observe it, you can't claim
compliance.

## Where the real execution layer plugs in

The companion `autonetops_ibn` project now carries the first working slice
of this layer, exercised against this very lab:

1. **Planner** (`autonetops-planner plan`): diffs two branches' artifact
   listings by checksum, computes blast radius over affected devices, and
   emits a staged, canary-first **Plan** (single-target canary + soak gate,
   then remainder). Protocol selection is two-axis: what the platform
   prefers AND what the artifact's `content_format` can ride — CLI text
   goes to cli/eAPI drivers; the moment a compiler emits model-based
   artifacts (ietf-xml / openconfig-json), NETCONF/gNMI win automatically.
2. **ibnctl** (`ibnctl --plan plan.yaml [--check]`): walks the Plan through
   executor drivers — resolve the device's address from the SoT, fetch the
   artifact content, verify its checksum, then snapshot → dry_run (a real
   diff from a private candidate / config session) → apply → rollback to
   the checkpoint on failure. Every verb appends to an evidence JSONL.
   Drivers exist for SR Linux and EOS (CLI), plus a generic NETCONF driver
   (candidate + confirmed-commit) awaiting model-based artifacts.
3. **Still pending:** a validator that loads `contract-expectations` and
   continuously evaluates them against Prometheus evidence, emitting
   `intent_compliance{contract=...} 0|1` — the gate ibnctl currently
   reports as "not evaluated". And auto-remediation for safe drift classes;
   invariant violations (a leak) page a human with the evidence attached.

## Caveats (honest lab notes)

- Control-plane fidelity is the goal; **dataplane MPLS forwarding on cEOS
  containers is limited** — expectations validate BGP/policy facts, which is
  where the intent story lives anyway.
- **`core-rr-01` was re-platformed twice, and that is the lesson.** It began
  as a vJunos router (which needs host CPU virtualization, `/dev/kvm`), moved
  to Nokia SR Linux to escape that, and is Cisco IOL now. SR Linux as a
  *vpn-ipv4 reflector* needs a 7250 IXR chassis, which containerlab only
  unlocks with a paid Nokia license — the free image emulates the 7220 IXR,
  which has no MPLS subsystem at all. Cisco IOL (ADVENTERPRISEK9) has full MPLS
  L3VPN, no license and no KVM, so the reflector lives there; the free SR Linux
  tier took over the customer edges, which need only plain eBGP.

  Each move was: one `DcimPlatform` field in `scripts/bootstrap.py`, the
  interface names that go with it, one `kind:` in the topology, one entry in
  `deploy_configs.py` for the transport. **Zero intent changes.** No contract,
  invariant, signal, check or expectation moved. `render_juniper_junos` and SR
  Linux's dormant reflector body stay registered and untouched, so moving the
  reflector back onto vJunos or a licensed 7250 is the same small edit.

- **The free SR Linux tier is a 7220 IXR, which is why it is a CE here.** MPLS
  and the `l3vpn-ipv4-unicast` address family exist only on the licensed 7250
  chassis (`ixr-6e`/`x1b`/…); on any 7220 fixed-form type — including clab's
  `ixr-d2l` default and the `ixr-d3l` this lab uses — `system mpls` is not even
  a config node and `protocols ldp` is not a valid token. A vpn-ipv4 reflector
  cannot be built there; a customer edge, which speaks only eBGP
  `ipv4-unicast`, can — so that is the role SR Linux plays.

- **The `mpls` capability still gates LDP in the renderer**, not a chassis
  check. `nokia_srlinux` no longer claims `mpls`, so SR Linux's dormant
  reflector body would emit no LDP block; add `mpls` back (on a licensed 7250)
  and it returns. Same switchboard as `gnmi` → collector selection.
- The CLI-scrape collector now has a natural target: **`core-rr-01` (Cisco IOL)
  claims `ssh_cli` and no `gnmi`**, so a signal scoped at `ibgp-core-rr`
  compiles `cli_bgp_telemetry.py` over SSH. The SR Linux CEs are gNMI-native on
  `:57400`, as are both cEOS PEs — see Phase 6.
- gNMI field naming varies by OS version; the starlark normalizer in
  `monitoring/telegraf/telegraf.conf` covers the common cases and is the one
  place to extend.

## Next: workshop 2

Day-2 operations live in [workshop2.md](workshop2.md): change the SoT on a
branch (interfaces, routing policy, advertisements), and let the
orchestrator render, diff, plan, schedule against SoT change windows, and
dispatch through validator-gated canary stages.
