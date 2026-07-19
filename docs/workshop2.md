# Workshop 2 — Intent-Based Networking: the theory, the operation, the road to scale

Workshop 1 built the pipeline: intent in InfraHub, compiled to configs,
expectations and telemetry, gated by invariants. This workshop finishes the
picture in three parts:

- **[Part I — Theory](#part-i--theory-what-intent-based-networking-actually-is)**
  defines intent-based networking properly: the concepts, the precise
  terminology (realm, intent, policy, contract, invariant, expectation, …)
  and the relationships between them — stated generally enough to fit most
  networks, then anchored to the exact InfraHub kinds this lab implements.
- **[Part II — Operation](#part-ii--operating-the-loop-hands-on)** is the
  hands-on lab: you change the SoT — interfaces, routing policy,
  advertisements — and the orchestrator plans, schedules and lands it
  through validator-gated canary stages.
- **[Part III — The plan](#part-iii--the-adoption-blueprint-automation-and-ai-at-scale)**
  is the adoption blueprint: the staged path from "we have an inventory and
  some scripts" to managing infrastructure with automation and AI at scale,
  including where AI agents plug in and the guardrails that make that safe.

Read Part I even if you have run the lab before — Part II's transcripts are
much louder once every word in them has a definition.

---

# Part I — Theory: what intent-based networking actually is

## 1. The problem IBN solves

A network device stores mechanisms: route-maps, prefix-lists, ACLs, VRFs.
It does not store *reasons*. The reasons — "customer-c bought dual-homed
L3VPN with a 200-route ceiling", "customer routes must never reach the
internet peers" — live in tickets, wiki pages, and the heads of the two
engineers who were there in 2019. Classic automation does not fix this: a
Jinja template driven by a spreadsheet makes the typing faster, but the
spreadsheet still stores mechanisms (which knob, which value), so the two
questions that actually matter stay unanswerable:

1. **Why is this line of config here?** (Which promise does it serve? What
   breaks if I delete it?)
2. **Is the network doing what we promised?** (Not "are the boxes up" —
   is every promise, to every tenant, demonstrably held *right now*?)

Intent-based networking inverts what gets stored. The **promise** becomes
the durable, versioned, reviewed artifact; the **mechanism** becomes
disposable compiler output; and every promise carries an **observable proof
obligation**, so "is the network doing what we promised" becomes a query,
not an archaeology project.

## 2. The definition

A network is intent-managed when six properties hold. Each maps to a
concrete object or process in this lab — nothing here is aspirational:

1. **Outcomes are declared as structured, versioned data** in a source of
   truth, not encoded in device config or scripts.
   (*InfraHub: `IntentIntent` and its graph, edited on branches.*)
2. **Constraints that must never break are first-class objects** that gate
   every change before it merges.
   (*`IntentInvariant` → merge-blocking checks on Proposed Changes.*)
3. **Mechanisms are compiled, never hand-written.** Vendor syntax, session
   lists, filters — all renderer output, regenerated at will.
   (*Transforms: contracts → EOS / SR Linux / IOS-XE / FRR configs.*)
4. **Every promise carries a proof obligation** stated in observable terms,
   with the collection method derived from what each platform can do.
   (*The `contract-expectations` artifact; capability-selected collectors.*)
5. **Delivery is planned, scheduled and gated by data** — blast radius is
   computed, canary strategy is reviewed policy, change windows live in the
   SoT, and rollout gates evaluate the same expectations as steady-state
   monitoring.
   (*`OpsChangeWindow`, the planner's Plan, the validator's verdicts.*)
6. **The loop closes.** Observed state is continuously compared with
   declared state; divergence is drift, and drift is either reconciled or
   escalated with evidence — never silently absorbed.
   (*Prometheus alerts on expectations; the reconcile/watch loop.*)

A useful mnemonic: the model answers six questions, and each question is a
distinct kind of object. Keeping them separate is most of the discipline:

| Question | Concept | InfraHub kind |
|---|---|---|
| **WHAT** outcome must hold? | Intent | `IntentIntent` |
| **WHO** is it for? | Tenant | `IntentTenant` |
| **WHERE** does it apply? | Zone | `IntentZone` |
| **HOW** is it realized? | Policy → Contracts | `IntentPolicy`, `IntentContract` |
| **WHAT MUST NEVER BREAK** while realizing it? | Invariant | `IntentInvariant` |
| **WITH WHAT** can each box participate? | Capability | `IntentCapability` (on `DcimPlatform`) |
| **WHEN** may change reach production? | Change window | `OpsChangeWindow` |
| **PROOF** that it holds? | Expectation → evidence | `contract-expectations` artifact → telemetry |

(That's eight rows for six questions — WHEN and PROOF are the two that
classic "source of truth" projects forget, and they are exactly the two
that make automation trustworthy at scale.)

## 3. The control loop

Everything in this repo and its companion is one loop. Every phase of
Part II is one arrow of it:

```
            ┌──────────────────────────────────────────────────────────┐
            │                    SOURCE OF TRUTH                       │
            │   realm → intent → policy → { contracts, invariants }    │
            │   + tenants, zones, capabilities, change windows         │
            └──────────────────────────────────────────────────────────┘
 DECLARE     branch → edit intent → Proposed Change     humans and AI agents,
                                                        same write path
 GATE        invariants run as checks on the PC;        design-time enforcement
             a blocking violation cannot merge
 COMPILE     contracts → artifacts: device configs,     merge = compile;
             expectations, collectors, alert rules,     mechanisms are output
             dashboards
 PLAN        diff branches → blast radius (computed)    nothing is estimated;
             → canary stages → timeline vs windows      the decision is
             → decision                                 reviewable data
 DISPATCH    per step: snapshot → dry_run → apply →     validator gates between
             rollback-on-failure; expectations gate     stages; baseline
             each stage                                 separates regression
                                                        from pre-existing drift
 OBSERVE     telemetry → metrics → alerts;              stale evidence is no
             expectations evaluated continuously        evidence
 RECONCILE   drift → re-plan (safe classes) or page     the loop closes; the
             a human with the evidence attached         SoT stays authoritative
```

Two structural facts about the loop matter more than any single stage:

- **There is exactly one write path.** Ticket, engineer, or AI agent — every
  change is a branch, a review, a merge. The 2 AM CLI session is not a
  faster lane; it is drift the loop will detect and chase.
- **The same expectations serve three masters**: rollout gates (did *this
  change* break the promise?), steady-state alerts (did *the world* break
  the promise?), and audit (show me the promise held in March). Write the
  proof once, at the intent layer, and every consumer inherits it.

## 4. Terminology — the IBN ontology

The words below are used precisely throughout this repo. Each entry gives
the definition, where it lives, and — just as important — what it is *not*,
because most failed IBN projects die of blurred vocabulary.

### Realm

A **domain of concern** that intents are declared in: `routing`,
`reachability`, `security`, `observability`, `reliability`, `compliance`,
`performance`. (*Kind: `IntentRealm`.*)

Realms are the most stable layer of the whole model — almost every network,
from a campus to a hyperscaler, needs roughly this same set. They exist to
keep intents honest about what they are promising and to give each realm
its own contract subtypes and invariant types. A realm is **not** an org
chart or a team; it is a category of promise.

### Intent

A **declared outcome the network must achieve**, scoped to exactly one
realm, usually owned by a tenant. (*Kind: `IntentIntent`.* Fields:
`statement` — the intent in one human sentence, "the contract with the
business"; `priority` critical/high/medium/low; `status`
draft/active/suspended/retired; `owner`, `ticket`.)

The test for a well-formed intent: **it survives a vendor swap and a
redesign unchanged.** "customer-c has resilient L3VPN connectivity between
its sites" is an intent. "configure `local-preference 150` on pe-emea-01"
is not — that is a mechanism wearing an intent's clothes, and it belongs
in renderer output. This lab re-platformed its route reflector twice
(vJunos → SR Linux → Cisco IOL) with **zero** intent edits; that is the
property you are buying.

Intent `priority` is business importance, and it *travels*: in Part II the
plan inherits `critical` from the intents whose contracts sit on the
touched devices. Priority is declared once, at the promise, and every
downstream decision (emergency eligibility, approval requirements) reads
it instead of re-asking.

### Policy

**How an intent is realized**: a named bundle of contracts plus the
invariants that guard them. (*Kind: `IntentPolicy`.* Field: `enforcement`
— `design_time` (invariants gate merges only), `runtime` (contracts are
telemetry-watched only), `full` (both).)

The policy is the **swap point**. When you change *how* — different
attachment design, different protocol, different vendor approach — you
retire one policy and attach another; the intent above it does not move.
A policy is **not** a "routing policy" in the vendor sense (route-maps are
compiled *from* contracts) and not a security rulebook (that's a contract
subtype). It is the seam between WHAT and HOW.

The `enforcement` axis is worth internalizing: `design_time` says "stop bad
changes", `runtime` says "watch the live promise", `full` says both. A
compliance policy might be design-time only (block the merge, nothing to
watch); a performance policy might be runtime only (nothing to gate, much
to measure).

### Contract

A **typed, machine-renderable promise** that realizes part of a policy.
(*Generic: `IntentContract`; subtypes per realm: `IntentRoutingContract`,
`IntentReachabilityContract`, `IntentSecurityContract`,
`IntentObservabilityContract`, `IntentReliabilityContract`.*)

Contracts are where intent becomes *precise* without becoming
*mechanical*. The routing contract `custc-ce-to-pe` declares: role
`customer_edge`, `peer_asn`, `afi_safis`, `local_preference`,
`max_prefixes_ipv4`, the communities to attach, the `allowed_prefixes`,
the PE and CE devices, the zone, and which tenants may never be exported
toward which zones. Note what is absent: no route-map name, no
prefix-list syntax, no vendor block — **a contract never names a
mechanism**. Try to find a field to put an SR Linux knob in; there isn't
one, by construction.

Contracts do three jobs at once, and all three compile from the same
object:

1. **Config source** — every renderer (EOS, SR Linux, IOS-XE, FRR, Junos)
   reads the same contract and emits its own grammar.
2. **Expectation source** — the same contract compiles to the proof
   obligations (sessions established, route counts within bounds,
   forbidden exports absent).
3. **Anchor to reality** — contracts reference real DCIM/IPAM objects
   (devices, prefixes, zones), so compilers know where artifacts land and
   checks know what universe to validate against.

A contract is **not** a config template (templates have vendor syntax) and
**not** an invariant (a contract says what *should* be; an invariant says
what must *never* be violated while getting there).

### Invariant

A **condition that must always hold**, declared as an object on the policy
it guards, compiled into a merge-gating check. (*Kind: `IntentInvariant`.*
Fields: `invariant_type` — `no_leak`, `no_reorigination`,
`no_default_origination`, `prefix_authorization`, `redundancy`,
`capability_present`, `reachability_preserved`, `oob_reachability`;
`severity` — `blocking` (a violation blocks the Proposed Change) or
`warning` (reported, does not block).)

Invariants are the network's constitution: they don't say what to build,
they say what no build may ever do. `custc-no-leak` doesn't configure
anything — it makes the state "customer-c routes reach the internet
peering zone" *unrepresentable in any merged version of the SoT*. In
workshop 1 you watched it: add customer-c to a peering contract's
`export_tenants` and the PC cannot merge, with a message naming the
tenant, the contract and the denied zone.

Crucially, invariants are **declared data, not tribal rules**. "We never
announce customer space to peers" as folklore dies with staff turnover; as
a blocking invariant it outlives everyone and applies equally to the new
hire and the AI agent.

### Expectation

The **runtime proof obligation** compiled from contracts: what must be
observable, via which collector, how fresh, for the promise to count as
held. (*Artifact: `contract-expectations`, one per contract, versioned and
diffable like any other artifact.*)

Expectations have a discipline of their own:

- **Ranges, never exact values** (`route_count <= 200`, not `== 173`) —
  exact values turn normal churn into false alarms.
- **Forbidden behavior is stated explicitly**
  (`export_prefixes_from_tenant(customer-c)` toward internet-peers must
  be *absent*) — you cannot prove a negative you never wrote down.
- **The collector is derived, not chosen** — `observedVia: gnmi` because
  the platform claims the `gnmi` capability; re-platform onto a box
  without it and the field flips to `cli_scrape` with no intent edit.
- **Freshness is part of the proof** (2× collection interval) — stale
  evidence is no evidence, and a dead collector must read as "unknown",
  never as "fine".

### Invariant vs expectation (the distinction that carries the model)

Both compile from the same intent, but they are enforcement on different
surfaces, and conflating them is the most common modeling error:

| | **Invariant** | **Expectation** |
|---|---|---|
| Evaluated over | the intent graph (the SoT itself) | the live network (telemetry) |
| Evaluated when | at review time, on every Proposed Change | continuously, and at rollout gates |
| Catches | a bad *change* before it merges | the *world* diverging after merge |
| Verdict acts by | blocking the merge | failing a gate / firing an alert |
| Example | "this prefix is outside the tenant's space → no merge" | "this session is not `established` → page sev2" |

An invariant stops you from *declaring* a broken world. An expectation
detects the world *becoming* broken on its own. You need both: a network
with only invariants is blind after merge; a network with only
expectations merges its own outages and then alerts on them.

### Tenant, Zone, Capability

The supporting vocabulary — each exists so that a critical question is
answered by a typed relationship instead of a naming convention:

- **Tenant** (*`IntentTenant`*) — WHO an intent serves: a customer, a
  partner, or the provider itself. Owns VRFs, carries the community that
  tags its routes, has an environment (dev/staging/prod). Invariants like
  no-leak and prefix-authorization are *tenant-relative* — they are
  meaningless without a first-class WHO.
- **Zone** (*`IntentZone`*) — WHERE, as a named region intents can refer
  to: `core`, `corp`, `transit`, `internet_peering`, `partner`,
  `management`. Zones let a contract say "never toward internet-peers"
  without enumerating devices.
- **Capability** (*`IntentCapability`*, attached to `DcimPlatform`) —
  WITH WHAT: `gnmi`, `ssh_cli`, `snmp`, `mpls`, … Capabilities are the
  switchboard between intent and mechanism: the observability compiler
  picks gNMI vs CLI-scrape off them, the renderer gates its LDP block on
  `mpls`, and the `capability_present` invariant refuses to merge a
  contract onto boxes that can't do what it needs. "This Cisco has no
  gNMI" lives *as data*, and everything downstream branches on it.

All three are **controlled vocabularies**, not free text. There is no
field where `"internet-peers "` (trailing space) can silently fracture the
model — and this is not pedantry: every machine decision downstream
branches on these values, so free text here is undefined behavior there.

### Change window

**WHEN, as reviewed data in the SoT.** (*Kind: `OpsChangeWindow`,
`schemas/operations.yml`.* Two shapes: recurring — `days`, `start_utc`,
`duration_minutes`; absolute — `starts_at`, `ends_at`. `window_type` is
`maintenance` (deployment allowed inside) or `freeze` (deployment of
matching devices blocked inside, whatever other windows say). Each window
carries a `max_blast_radius` ceiling and a device scope; empty scope =
fleet-wide.)

The intent model says WHAT must hold and HOW it materializes; windows say
WHEN a plan may touch production. Putting them in the SoT — versioned,
reviewed, queried — instead of a YAML file on an operator's laptop means
the scheduler's inputs have the same provenance as the intent itself.

### Plan

The **content-addressed compilation of one specific change**: what will
touch which devices, in what order, inside which windows, requiring what
approvals. (*Produced by the companion `autonetops_ibn` planner; an
immutable directory: `plan.yaml`, `timeline.yaml`, `decision.yaml`,
checksummed `content/*.cfg`, `expectations/*.yml`.*)

Content-addressed means: same diff + same policy ⇒ same plan id — so the
rollout is reviewable *before* anyone clicks merge, and a merged (deleted)
branch can no longer change what was reviewed. Dispatch replays the
plan's exact bytes, checksum-verified, never a fresh render.

### Blast radius

The **computed** set of devices whose rendered artifacts differ between
the two branches, classed `low` / `moderate` / `high`. Nobody lists the
devices; the renderer + diff find them — in Part II, three SoT edits fan
out to four devices, including two CEs no one mentioned. Blast radius
selects the canary strategy and determines which windows admit the plan.
It is a property of *a change*, never of an intent.

### Gate, baseline, verdicts

A **gate** is the validator run between rollout stages: the candidate
branch's expectations, scoped to the stage's devices (including sessions
*observed elsewhere* — change a CE, prove it on the PE facing it),
evaluated against live facts. Four verdicts, all loud:

- `pass` — observed, and the expectation holds.
- `fail` — observed, and it doesn't: the plan aborts, snapshots named.
- `pre_existing` — it fails, but it also failed in the **baseline** taken
  before step one. A gate blocks on what the change *broke*, not on what
  was already broken; pre-existing failures become drift for the
  reconcile loop, not rollout aborts.
- `unverifiable` — no driver/collector can observe it. It neither fails
  the gate nor counts as passing. **Unobservable is never green.**

### Evidence, drift, reconciliation

**Evidence** is the append-only record: every executor verb (snapshot,
dry_run, apply, rollback), every gate verdict, every override, in
`evidence.jsonl` — the audit trail is a by-product of operating, not a
report written afterwards. **Drift** is any divergence between declared
and observed state: config drift (device differs from rendered artifact)
or promise drift (expectation stops holding). **Reconciliation** is the
loop that chases it: re-render and re-diff (convergent re-dispatch is
idempotent — "no diff - already converged"), auto-remediate safe classes,
and page a human *with the evidence attached* for the rest. Invariant
violations discovered live (a leak) always page — machines fix drift,
humans decide exceptions.

### Three axes that look alike (and must not be conflated)

| Axis | Lives on | Vocabulary | Answers |
|---|---|---|---|
| **priority** | Intent | critical / high / medium / low | How important is this outcome to the business? Inherited by plans; gates emergency eligibility. |
| **severity** | Invariant; observability signal | blocking / warning; sev1…sev4 | How bad is a violation of this specific rule/signal? |
| **blast radius** | Plan (computed); window (ceiling) | low / moderate / high | How big is this specific change? |

A `critical`-priority intent can receive a `low`-blast-radius change
(tweak one value) guarded by a `blocking` invariant. Three different
questions, three different objects.

### The relationship map

Cardinalities as implemented in `schemas/intent.yml` /
`schemas/operations.yml`:

```
IntentTenant (WHO)              IntentRealm (domain of concern)
      │ 0..1                          │ 1
      └────────────┐                  │
                   ▼                  ▼
                * IntentIntent ───────┘     statement, priority, status
                     │ 1                    (the WHAT; survives redesigns)
                     ▼
                * IntentPolicy              enforcement: design_time |
                     │                                   runtime | full
        ┌────────────┴─────────────┐
        ▼                          ▼
  * IntentContract           * IntentInvariant
   (typed promise —           (must always hold —
    feeds the compilers)       feeds the merge gates)
        │ anchors                  severity: blocking | warning
        ├── * DcimGenericDevice   (pe_devices / ce_devices)
        ├── * IpamPrefix          (allowed_prefixes, to_prefixes)
        ├── 1 IntentZone          (+ export_deny_zones)
        ├── * IntentTenant        (export_tenants)
        └── watched by * IntentObservabilitySignal (condition, severity,
                                                    frequency)

DcimPlatform ── * IntentCapability     selects collectors, gates renderer
                                       arms, checked by capability_present
OpsChangeWindow ── * DcimGenericDevice WHEN; maintenance | freeze;
                                       max_blast_radius; empty scope = fleet
```

Derived from that graph, never stored in it: device configs,
expectations, collector configs, alert rules, dashboards, plans, evidence.
If you can regenerate it, it is not intent.

## 5. The design laws

Ten laws, each earned in this lab, each portable to any network:

1. **Intent is stable; renderers change.** The reflector moved across
   three vendors; no contract, invariant, signal, check or expectation
   moved with it. If a redesign forces intent edits, mechanism has leaked
   into your intent layer.
2. **Expectations are the proof.** A promise without an observable proof
   obligation is a hope. Every contract compiles to expectations; every
   expectation names its collector and freshness.
3. **Declare outcomes, derive mechanisms.** Sessions are derived from
   contract + cabling; the CE renderer reads the provider ASN off the
   modeled far end of the cable. Every derived fact is one you cannot
   fat-finger and never have to update.
4. **Controlled vocabularies beat free text.** Machines branch on these
   values; free text is undefined behavior.
5. **Constraints are objects, not tribal knowledge.** An unwritten rule
   cannot block a merge, survive turnover, or bind an AI agent.
6. **Everything operational is reviewed data.** Change windows, canary
   strategies, priorities — in the SoT or in reviewed policy files, never
   in someone's terminal history.
7. **One write path for humans and machines.** Branch → review → merge →
   compile → plan → dispatch. Break-glass exists (`ibnctl`,
   `--override-windows`, `--emergency`) but goes on the record.
8. **Verdicts are honest.** pass / fail / pre_existing / unverifiable.
   A gate that can't observe something says so; it never rounds
   "unknown" up to "fine". Rate limits report `not_evaluated` until an
   evaluator exists.
9. **Blast radius is computed, not estimated.** Render everything, diff
   checksums, let the fan-out surprise you *before* dispatch. Humans are
   terrible at guessing transitive impact; compilers are not.
10. **Convergence over execution.** Dispatch is idempotent: re-running a
    landed plan applies nothing ("no diff - already converged"). Systems
    that converge can be retried, resumed, and reasoned about; systems
    that merely execute can only be babysat.

## 6. Fitting this to *your* network

The lab is a service-provider L3VPN because that story exercises every
concept at once (tenancy, leak risk, redundancy, multi-vendor). The
*model* is not SP-specific — realms and the object grammar stay fixed;
what changes per environment is which contract subtypes you write and
which invariant types matter most:

| Environment | Typical contracts | Typical blocking invariants | Typical expectations |
|---|---|---|---|
| **SP / WAN** (this lab) | routing (CE↔PE, core, peering), reachability | no_leak, prefix_authorization, redundancy | sessions established, route counts bounded, forbidden exports absent |
| **DC fabric** (EVPN/VXLAN) | tenancy (VRF/VNI membership), reachability between segments | no cross-tenant leak, symmetric IRB everywhere, redundancy (dual ToR) | VTEP peering full-mesh, type-2/type-5 route presence, ECMP width |
| **Campus / enterprise** | segmentation (user/IoT/guest zones), QoS class promises | no path from IoT zone to corp data, redundancy per closet, capability_present (dot1x) | SSID→VLAN→VRF chain intact, posture enforcement observed, convergence budget |
| **Cloud / hybrid** | connectivity (VPC↔on-prem), egress policy | no unintended public exposure, prefix_authorization on advertised ranges | tunnels/attachments up, advertised == allowed, latency within budget |
| **Security overlay** (any of the above) | filtering posture per device/interface role | default-deny present, management plane ACL'd, oob_reachability | rule hit-counters live, forbidden flows absent, collector freshness |

How to choose what to model first (this matters more than the tooling):

- **Model the promises you already make.** SLAs, compliance commitments,
  the security rules auditors ask about — they are intents already; they
  are just stored in PDFs.
- **Model what pages you.** Every recurring incident class is an
  expectation you haven't written down yet. Each one you formalize turns
  a 2 AM mystery into a named, evidenced verdict.
- **Model the rules people recite in reviews.** "We never announce
  customer space to peers" recited in every design review is a blocking
  invariant waiting to be an object.
- **Do not model everything.** Vendor knobs, cosmetic naming, one-off
  hacks — leave them out or lose the stability that makes intent worth
  storing. The test from §4 applies to every candidate field: would it
  survive a vendor swap? If not, it belongs in a renderer.

With the vocabulary in hand, run the loop.

---

# Part II — Operating the loop (hands-on)

Workshop 1 built the pipeline; this part is about **operating** it: you
change the SoT — interfaces, routing policy, advertisements — and the
orchestrator does the rest:

```
you: branch ─ edit ─ propose ─ merge          (InfraHub, reviewed)
orchestrator: render ─ diff ─ plan            (blast radius → canary stages)
              evaluate timeline               (SoT change windows + freezes)
              decide                          (immediate/scheduled/approval/blocked)
              dispatch                        (executor verbs + validator gates)
```

Everything the orchestrator decides comes from data you can see: windows in
the SoT, canary strategies in reviewed policy files, priority inherited from
the intents themselves. Nothing is typed into a terminal at 2 AM.

Companion repo: [`autonetops_ibn`](../../autonetops_ibn) — planner, executor
drivers, validator, and the `ibn-orchestrate` CLI (ADR-0013 documents the
design).

---

## Phase 0 — Prerequisites

Workshop 1 completed: InfraHub bootstrapped, lab running, artifacts render.
Then:

```bash
cd ../autonetops_ibn
uv pip install -p .venv/bin/python -e ./planner -e ./executor -e ./validator \
    -e ./orchestrator -e ./tools/ibnctl
export INFRAHUB_ADDRESS=http://localhost:8000
export INFRAHUB_API_TOKEN=<your token>       # same one infrahubctl uses
export IBN_SOT_REPO=/root/autonetops_infrahub
```

Re-run `python scripts/bootstrap.py` in this repo if you haven't since the
`schemas/operations.yml` schema landed — it seeds the change windows below.

---

## Phase 1 — WHEN lives in the SoT: change windows

*Concepts in play: [change window](#change-window), [blast radius](#blast-radius)
as a window ceiling, design law 6 (everything operational is reviewed data).*

`schemas/operations.yml` adds one kind, `OpsChangeWindow`. Windows are
operational intent: reviewed, versioned, queried — never a YAML file on
someone's laptop. Two shapes share the kind:

| shape     | fields                                  | typical use            |
|-----------|------------------------------------------|------------------------|
| recurring | `days`, `start_utc`, `duration_minutes` | weekly maintenance     |
| absolute  | `starts_at`, `ends_at`                  | one-offs and freezes   |

Each window carries a `max_blast_radius` ceiling (the largest plan class
allowed to ride it) and a device scope (empty = fleet-wide).
`window_type: freeze` inverts the meaning: matching devices are untouchable
inside it.

Look at what bootstrap seeded:

```bash
ibn-orchestrate windows
```

```
emea-standard-maintenance        maintenance  max_blast=high      next: 2026-07-14T21:00:00+00:00 .. 2026-07-15T01:00:00+00:00
                                 scope: core-rr-01, pe-emea-01, pe-emea-02
lab-continuous                   maintenance  max_blast=moderate  next: 2026-07-12T00:00:00+00:00 .. 2026-07-13T00:00:00+00:00
                                 scope: all devices
q3-core-audit-freeze             freeze       max_blast=moderate  next: 2026-07-20T00:00:00+00:00 .. 2026-07-27T00:00:00+00:00
                                 scope: core-rr-01
weekday-low-risk                 maintenance  max_blast=low       next: 2026-07-13T08:00:00+00:00 .. 2026-07-13T18:00:00+00:00
                                 scope: all devices
```

Read that as policy: low-risk changes may ride business hours anywhere;
anything up to *moderate* may deploy any time in the lab (delete
`lab-continuous` and you get production behavior); *high* blast radius waits
for Tuesday/Thursday night — and during the Q3 audit, nobody touches the
route reflector, whatever the other windows say.

In the UI: **Object Management → Change Window**. Via GraphQL:

```graphql
query { OpsChangeWindow { edges { node {
  name { value } window_type { value } days { value }
  start_utc { value } duration_minutes { value }
  max_blast_radius { value }
  devices { edges { node { display_label } } }
}}}}
```

---

## Phase 2 — The change, on a branch

*Concepts in play: [intent vs mechanism](#intent), [contract](#contract),
the one write path (design law 7), [invariants](#invariant) on the branch.*

The business ask, three SoT edits (this is the whole workshop change):

1. **Advertisement** — customer-c activates site range `10.84.40.0/24`;
   it must be accepted from their CEs and carried in `CUSTC-PROD`.
2. **Routing policy** — customer-c routes win over any backup path:
   `local_preference` 120 → 150.
3. **Interfaces** — a services loopback (`Loopback1`) on both PEs.

Note what is **not** in the list: no route-maps, no prefix-lists, no
`set / network-instance ...` lines. Those are renderer output.

### 2a. The repeatable way (SDK script)

```bash
python scripts/workshop2_change.py            # creates the branch + edits
python scripts/workshop2_change.py --propose  # same + opens the Proposed Change
```

The script is idempotent — read it; it is the reference for the mutations
below.

### 2b. The UI way

1. Branch selector (top bar) → **+** → name it `workshop2-cust-c-expansion`
   → *Create*. Everything you now edit happens on the branch.
2. **IPAM → Prefixes → +**: `10.84.40.0/24`, status `active`,
   VRF `CUSTC-PROD`.
3. **Intent → Contract → custc-ce-to-pe**: edit `local_preference` to `150`;
   under *allowed_prefixes* add `10.84.40.0/24`.
4. **Device Management → Devices → pe-emea-01 → Interfaces → +**:
   kind *Interface Virtual*, name `Loopback1`, status `active`, description
   `services anchor [workshop2]`, IP `10.255.10.1/32`. Repeat on
   `pe-emea-02` with `10.255.10.2/32`.

### 2c. The API way (GraphQL, on the branch)

Every mutation below runs against the branch — note the URL:
`POST /graphql/workshop2-cust-c-expansion` (or `branch_name` in the SDK).

```graphql
mutation { BranchCreate(data: {name: "workshop2-cust-c-expansion",
                               sync_with_git: false}) { ok } }
```

```graphql
mutation {
  IpamPrefixUpsert(data: {
    prefix: {value: "10.84.40.0/24"}, status: {value: "active"},
    vrf: {hfid: ["CUSTC-PROD"]},
  }) { ok object { id } }
}
```

```graphql
mutation {
  IntentRoutingContractUpdate(data: {
    hfid: ["custc-ce-to-pe"],
    local_preference: {value: 150},
  }) { ok }
}
```

Adding to a many-relationship without replacing it uses `RelationshipAdd`
(ids from the queries/UI):

```graphql
mutation {
  RelationshipAdd(data: {
    id: "<contract-id>", name: "allowed_prefixes",
    nodes: [{id: "<prefix-id>"}],
  }) { ok }
}
```

```graphql
mutation {
  InterfaceVirtualUpsert(data: {
    name: {value: "Loopback1"},
    device: {hfid: ["pe-emea-01"]},
    status: {value: "active"},
    description: {value: "services anchor [workshop2]"},
    ip_addresses: [{id: "<ip-id>"}],
  }) { ok }
}
```

### 2d. Review the branch

UI: **Branches → workshop2-cust-c-expansion → Diff** shows every node/attr/
relationship delta. The invariants run on the branch too:

```bash
export COLUMNS=4000   # rich hard-wraps piped output otherwise
infrahubctl check --branch workshop2-cust-c-expansion \
    contract_prefix_authorization_check contract=custc-ce-to-pe
# PASSED - the new prefix is inside customer-c's VRF; move it outside and
# this check is exactly what blocks the merge
```

---

## Phase 3 — Plan: blast radius, timeline, decision

*Concepts in play: [plan](#plan), [blast radius](#blast-radius) (computed,
law 9), priority inheritance (§4, [three axes](#three-axes-that-look-alike-and-must-not-be-conflated)),
[windows](#change-window) as timeline input.*

```bash
cd ../autonetops_ibn
ibn-orchestrate plan --candidate workshop2-cust-c-expansion
```

Real output from this lab:

```
plan plan-af0745e92008abea
  blast radius : moderate (4 devices: ce-custc-01, ce-custc-02, pe-emea-01, pe-emea-02)
  strategy     : builtin-single-target-canary
  priority     : critical (from provider-core-transport)
  stage-1-one_target           -> 2026-07-12T01:51:10+00:00 via lab-continuous
  stage-2-remainder            -> 2026-07-12T02:24:10+00:00 via lab-continuous
  decision     : immediate
      - window 'lab-continuous' is open for blast radius 'moderate'
      - priority 'critical' inherited from intent(s): provider-core-transport
  saved to     : plans/plan-af0745e92008abea/
```

Unpack every line:

- **4 devices** — the orchestrator rendered *every* device on both branches
  (same queries, same transform classes the server uses) and diffed
  checksums. Three SoT edits fan out to two PEs (prefix-list, route-map,
  Loopback1) and two CEs (static anchor + prefix-set for the new
  advertisement). Nobody listed those devices; the diff found them.
- **moderate → single-target canary** — blast-radius class picks the canary
  strategy from `planner/policies/canary_strategies.yaml` (built-ins while
  the file is a draft): one target first, 30-minute soak, then the rest.
- **priority critical** — inherited, not typed: the changed PEs carry the
  `provider-core-transport` intent's contracts, and the highest intent
  priority on the touched devices wins.
- **the two timestamps** — stage 2 may not start before stage 1's soak ends
  (01:51 + ~3 min execution + 30 min soak = 02:24), and both fit inside an
  eligible window. `timeline.yaml` has the full schedule.
- **decision: immediate** — a window admitting `moderate` is open right
  now. The other modes: `scheduled` (sound, but waits), `needs_approval`
  (strategy demands named `--approve`s), `blocked` (no window in 14 days).

The plan directory is the immutable hand-off:

```
plans/plan-af0745e92008abea/
  plan.yaml           # the Plan contract (stages, steps, rollback, checksums)
  timeline.yaml       # when each stage may run, and why
  decision.yaml       # mode + reasons + priority + approval requirements
  content/*.cfg       # the exact rendered payloads, checksum-locked
  expectations/*.yml  # ContractExpectations from the candidate branch
```

Dispatch later replays *these* bytes — a merged (deleted) branch can no
longer change what was reviewed, and each payload is checksum-verified
against the plan before touching a device.

---

## Phase 4 — Propose, watch, merge

*Concepts in play: the [control loop](#3-the-control-loop)'s DECLARE→GATE→PLAN
arrows; content-addressed plans; reconciliation ("webhooks trigger,
reconciliation guarantees").*

Open the Proposed Change (if you didn't in Phase 2):

```bash
python scripts/workshop2_change.py --propose
```

The reconcile loop follows PCs ("webhooks trigger, reconciliation
guarantees" — every pass re-reads all of them, so a missed event costs one
interval, never a deployment):

```bash
ibn-orchestrate watch --once --soak-scale 0.02
```

```
[watch] proposed change 'workshop2: customer-c expansion' (workshop2-cust-c-expansion) is open - planning
plan plan-af0745e92008abea
  ...
  decision     : immediate
```

Same plan id — plans are content-addressed: same diff + same policy, same
plan. The rollout is reviewable **before** anyone clicks merge.

Now the human step: review the PC in the UI (checks green, artifact diffs
sane) and **Merge**. Then run the watcher again (or leave it running without
`--once`):

```bash
ibn-orchestrate watch --once --soak-scale 0.02
# [watch] 'workshop2: customer-c expansion' merged - dispatching plans/plan-af0745e92008abea
```

---

## Phase 5 — Dispatch anatomy (a true story, three acts)

*Concepts in play: [gates, baseline, the four verdicts](#gate-baseline-verdicts)
(law 8 — verdicts are honest), [evidence](#evidence-drift-reconciliation),
convergence (law 10).*

Everything below is the real transcript of this change landing on this lab
(`--soak-scale 0.02` turns the 30-minute soak into 36 s for the workshop).

### Act 1 — the gate earns its keep

```
== stage 1/2: stage-1-one_target (1 step(s); devices: ce-custc-01)
  ce-custc-01 [device-configuration] APPLIED chg-89252c1f3f04 (snapshot ibn-c1b751eb4753)
  gate: soaking 36s (soak 30m x scale 0.02)
    [ok] pe-emea-01 -> 10.84.255.2 (custc-ce-to-pe): established
  gate: 1 pass

== stage 2/2: stage-2-remainder (3 step(s); devices: ce-custc-02, pe-emea-01, pe-emea-02)
  ce-custc-02 [device-configuration] APPLIED chg-cb6f7e080070 (snapshot ibn-964940bd5a37)
  pe-emea-01 [device-configuration] APPLIED chg-583bfa7b04e0 (snapshot ibn-bb108d303835)
  pe-emea-02 [device-configuration] APPLIED chg-30614f2f1bc1 (snapshot ibn-3056530ff863)
    [ok] pe-emea-01 -> 10.84.255.2 (custc-ce-to-pe): established
    [ok] pe-emea-02 -> 10.84.255.6 (custc-ce-to-pe): established
    [??] core-rr-01 -> 10.100.0.0 (ibgp-core-rr): unobserved
    [??] core-rr-01 -> 10.100.0.2 (ibgp-core-rr): unobserved
    [FAIL] pe-emea-02 -> 203.0.113.1 (inet-peering-emea): active
  gate: 1 fail, 2 pass, 2 unverifiable
ABORT: gate failed after stage-2-remainder: 1 fail, 2 pass, 2 unverifiable;
       snapshots are on the devices for manual revert
```

Every changed session verified established — and the plan still aborted,
because `pe-emea-02` carries the internet-peering contract and *that*
session is down (`active` = the peer isn't answering; in this lab
`peer-inet-01` has been unconfigured since the lab host moved). On its first
live run the gate caught **real drift the change didn't cause**.

### Act 2 — regressions block; history doesn't

Correct verdict, wrong consequence: a rollout gate should block on what the
change *broke*, not on what was already broken. So dispatch takes a
**baseline** before step one — the same expectations, evaluated against the
pre-change network — and a failure that predates the change is reported as
`pre_existing` (drift for the reconcile loop to chase) instead of aborting
the rollout:

```
baseline: 1 fail, 2 pass, 2 unverifiable
  pre-existing: pe-emea-02 -> 203.0.113.1 (inet-peering-emea) is active; gates will not block on it
```

### Act 3 — convergence

Re-dispatching the same plan (this is also the idempotence proof — the
first run had already applied everything before the abort):

```
== stage 1/2: stage-1-one_target (1 step(s); devices: ce-custc-01)
  ce-custc-01 [device-configuration] no diff - already converged
    [ok] pe-emea-01 -> 10.84.255.2 (custc-ce-to-pe): established
  gate: 1 pass

== stage 2/2: stage-2-remainder (3 step(s); devices: ce-custc-02, pe-emea-01, pe-emea-02)
  ce-custc-02 [device-configuration] no diff - already converged
  pe-emea-01 [device-configuration] no diff - already converged
  pe-emea-02 [device-configuration] no diff - already converged
    [ok] pe-emea-01 -> 10.84.255.2 (custc-ce-to-pe): established
    [ok] pe-emea-02 -> 10.84.255.6 (custc-ce-to-pe): established
    [??] core-rr-01 -> 10.100.0.0 (ibgp-core-rr): unobserved
    [??] core-rr-01 -> 10.100.0.2 (ibgp-core-rr): unobserved
    [pre] pe-emea-02 -> 203.0.113.1 (inet-peering-emea): active
  gate: 2 pass, 1 pre_existing, 2 unverifiable

done. failures: 0
```

Read the machinery out of those transcripts:

- **The verb sequence per step** (in `evidence.jsonl`): `snapshot` (device
  checkpoint) → `dry_run` (vendor-native diff: EOS config session, SR Linux
  private candidate) → `apply` → rollback-on-failure. One implementation,
  `autonetops_executor.run_step`, shared by `ibnctl` (break-glass) and the
  orchestrator — the sequence cannot drift between them.
- **The gate is the validator, live**: the candidate branch's
  ContractExpectations are scoped to the stage's devices — *including
  sessions observed elsewhere*: stage 1 changed only `ce-custc-01`, and its
  proof is the PE session facing it. Facts come through the executor
  (`get_facts` returns parsed `bgp_neighbors`).
- **Four verdicts, all loud**: `pass`, `fail` (a regression aborts the plan
  with the snapshots named for revert), `pre_existing` (predates the
  change), `unverifiable` — `core-rr-01` (Cisco IOL) has no executor driver
  yet, so its sessions are reported as unobservable; they neither fail the
  gate nor count as passing.
- **Windows are re-checked at dispatch time** — the plan's timeline was a
  forecast, not a permission. Try it: `dispatch` outside a window exits `3`
  and tells you when the window opens (`--wait` sleeps until then;
  `--override-windows` is for the lab and goes on the record).

Verify the intent landed, from the devices' point of view:

```
core-rr-01# show bgp vpnv4 unicast all | include 10.84.40
 * i  10.84.40.0/24    10.255.0.2                    150      0 65123 ?

pe-emea-02# show ip bgp 10.84.40.0/24 vrf CUSTC-PROD
  65123
    10.84.255.6 from 10.84.255.6 (10.84.255.6)
      Origin INCOMPLETE, metric 0, localpref 150, ... best
      Community: 65010:1203 65010:30010 65010:55555
  65123
    10.255.0.1 from 10.255.0.3 ... imported VPN-IPv4 route, RD 65010:1203

pe-emea-01# show ip interface brief | include Loopback1
Loopback1         10.255.10.1/32        up           up
```

The new range is advertised by the CEs, accepted by the contract's
prefix-list, tagged with the contract communities, preferred at 150, and
reflected through the core — every device configured by the orchestrator,
nothing by hand.

And prove convergence the SoT way — after the merge, re-plan:

```bash
ibn-orchestrate plan --candidate main   # or re-run against the branch
# no artifact differences; nothing to plan
```

---

## Phase 6 — Exercises

1. **Production scheduling.** Delete the `lab-continuous` window (UI or
   GraphQL), re-run `ibn-orchestrate plan --candidate ...`: the same
   moderate plan now schedules for Tuesday 21:00 via
   `emea-standard-maintenance` and the decision flips to `scheduled`.
   Dispatch exits `3` (waiting). Re-run `scripts/bootstrap.py` to restore.
2. **The freeze.** `ibn-orchestrate windows --at 2026-07-21T12:00:00Z`
   shows the Q3 freeze active. A plan touching `core-rr-01` evaluated at
   that time (`plan --at ...`) defers past July 27 — a freeze truncates or
   defers slots, it never silently shrinks a plan.
3. **Approvals.** Edit `planner/policies/canary_strategies.yaml`: give the
   moderate strategy `requires_approvals: [platform-lead]`. The decision
   becomes `needs_approval` and dispatch refuses until
   `--approve platform-lead` — the approval lands in the decision record.
4. **Fail the gate.** Shut the CE-facing port on the canary's PE
   (`interface Ethernet2; shutdown` on pe-emea-01) and dispatch a fresh
   change: stage 1's gate reports `fail: absent`, the plan aborts, stage 2
   never runs, and the pre-change snapshots are named in the output.
5. **Emergency, on the record.** With `lab-continuous` deleted, add
   `--emergency` to a plan whose devices carry a `critical` intent: the
   decision is `immediate` with `emergency_override: true`, and the
   override is written into `evidence.jsonl`. Try the same with a
   `medium`-priority change — refused: emergencies need critical intent.
6. **Rate limits** are still `not_evaluated` in `policy_evaluation` —
   honest until an evaluator exists. Write one: `planner/policies/
   rate_limits.yaml` has the draft shape.

---

# Part III — The adoption blueprint: automation and AI at scale

Part I gave you the vocabulary; Part II showed the loop running on a lab.
This part is the plan for *your* network. It is staged deliberately: each
stage is independently valuable, each produces the data the next one
needs, and none requires a big bang. The single most common way IBN
initiatives die is attempting stage 4 before stage 1 — automating delivery
of a model nobody trusts yet.

## Stage 0 — A source of truth worth trusting

**The work.** Get the physical and logical inventory into the SoT:
devices, platforms **with capabilities**, interfaces, cabling (including
the OOB plane), IP space, VRFs, locations. Model only what the first realm
you pick will need — a complete model of everything is a project that
never ships.

**The keystone habit.** Flip the direction of record: the SoT stops being
where reality is *documented afterwards* and becomes where it is
*declared first*. A device exists because it was created in the SoT and
then racked, not the other way around.

**Exit criteria.** New devices enter through the SoT; platform
capabilities are data (`gnmi`, `ssh_cli`, `snmp`, `mpls`, …); cabling for
the target domain is modeled. You can answer "what is attached to what,
and what can each box do?" with a query.

## Stage 1 — Intent, read-only: expectations before automation

**The work.** Pick **one realm and one service** you already promise
(here: routing + customer-c's L3VPN). Model the chain — realm → intent →
policy → contracts — and compile **only** expectations, collectors,
alerts and dashboards. Push no configs. The network keeps being managed
however it is managed today.

**Why this is the highest-leverage stage.** You now continuously verify
promises you were already making but never checking. Every alert that
fires is a real, previously invisible defect — and every one builds the
organizational trust you will spend in stage 4. This is also where the
model gets honest: writing `allowed_prefixes` for a real customer forces
the conversation about what was actually promised.

**Exit criteria.** Every declared contract has expectations evaluated
continuously against the live network; a violated promise is detected
within one collection interval; the team reads the dashboards.

## Stage 2 — Invariants gate every change

**The work.** Route SoT changes through branches and Proposed Changes —
even while configs are still delivered by hand — and write your first
blocking checks. Start with the rules people recite in design reviews:
prefix authorization, no-leak, minimum redundancy, capability presence.

**The payoff.** Each blocked merge is an outage that didn't happen, with
a named invariant and a message explaining itself. This is also the stage
where review changes character: reviewers approve *intent deltas* ("new
range for customer-c, local-pref 150") instead of eyeballing config
diffs.

**Exit criteria.** ≥90% of changes to the modeled domain enter via PC;
every blocking invariant has a check; violations-caught-pre-merge is a
number you report.

## Stage 3 — Compile, then converge (the brownfield stage)

**The work.** Write/adopt renderers for one platform and one contract
class, then run the **render-and-diff loop**: compare rendered artifacts
against running config, device by device. Every diff is one of two
things — a **model gap** (reality is right, the model/renderer is
incomplete: fix the model) or **drift** (the model is right, the device
accumulated history: schedule remediation). Adopt device-by-device; never
big-bang a fleet.

**The vendor dividend.** The moment two platforms render from the same
contract, mechanism has provably left your intent layer — this lab's
reflector hopping vendors on a one-field platform change is the proof
pattern. That independence is what you'll later spend on hardware
refreshes and AI-driven changes alike.

**Exit criteria.** Converged-fleet %: the share of devices where
re-render produces no diff. Growing that number *is* the migration.

## Stage 4 — Orchestrated delivery

**The work.** Everything in Part II: change windows into the SoT, the
planner computing blast radius from rendered diffs, canary strategies as
reviewed policy files, dispatch with snapshot → dry_run → apply →
rollback, gates evaluating expectations per stage, a baseline separating
regressions from pre-existing drift, evidence appended per verb.

**The rule that keeps it safe.** The plan is content-addressed and
reviewed *before* merge; dispatch replays checksummed bytes inside
windows it re-checks at runtime. Emergencies exist (`--emergency`,
gated on `critical` intent priority) — on the record, never as a habit.

**Exit criteria.** Change failure rate and rollback MTTR trending down;
% of changes landing with zero human keyboard time on devices trending
up; every dispatch reconstructable from its evidence.

## Stage 5 — The closed loop, and AI as an operator

**The work.** Leave the watcher running: reconciliation plans on PC open,
dispatches on merge, chases drift continuously. Auto-remediate the safe
drift classes (convergent re-dispatch of already-reviewed state is the
safest write a machine can make). Page humans only for what machines must
not decide: invariant violations, emergencies, novel intent — always with
the evidence attached.

Then, and only then, add AI throughput. The stages were not a detour on
the way to AI — they *are* the AI-readiness program:

### Why this architecture is AI-ready by construction

- **The schema is the tool contract.** A typed GraphQL model with
  controlled vocabularies is exactly what an LLM agent can operate
  reliably — no screen-scraping, no free-text guessing, and mutations
  that fail loudly on invalid values.
- **Invariants make agent alignment structural, not promptable.** You do
  not need to trust an agent's judgment about leaks; the no-leak check
  blocks its merge exactly as it blocks yours. Guardrails live in the
  target system, where no prompt injection can unwrite them.
- **Checks are an objective reward signal.** An agent can iterate on a
  branch until checks pass *before* any human spends attention — the
  review loop is machine-grindable, the merge is not.
- **Content-addressed plans make agent work reproducible and reviewable.**
  What the agent proposed is exactly what dispatch will replay.
- **Evidence closes the agent's loop.** Gate verdicts, alert context and
  drift reports are structured feedback an agent can actually learn a
  workflow from — and an audit trail humans can actually check.

### The AI operating model

Three rules, then the roles:

1. **An agent's interface is the SoT, never the device.** Agents read via
   GraphQL, write on branches, open Proposed Changes. No agent holds
   device credentials; the executor — deterministic, checksum-verified,
   window-checked — is the only thing that touches boxes.
2. **Agents ride the same write path as humans** — branch → checks →
   review → merge → plan → dispatch — with the same gates. Autonomy is
   then a *dial*, not a leap: start with human merge on every agent PC;
   as an agent class proves itself, delegate merges for low-blast-radius,
   all-checks-green changes; blocking-invariant overrides and emergency
   dispatches remain human forever.
3. **Separation of duties is a merge rule.** No actor — human or agent —
   merges a change to a check together with a change the check gates.
   The agent that proposes doesn't approve; the agent that approves
   doesn't override.

| Agent role | Reads | Writes | Human touchpoint |
|---|---|---|---|
| **Intake translator** | tickets, the schema | branch edits + a PC ("customer-x, new site, range Y") | reviews the PC — an intent delta, not 200 config lines |
| **Drift triager** | alerts, evidence, expectations | drift classification; re-dispatch proposals for safe classes | approves remediation outside the safe list |
| **Plan explainer** | plan/timeline/decision dirs | natural-language rollout summaries on the PC | reads a paragraph instead of four YAML files |
| **Invariant author's assistant** | incident reports, the graph | *draft* invariants + checks from postmortems | every new gate is human-merged, always |
| **What-if analyst** | the SoT, on scratch branches | throwaway branches: "blast radius if we re-platform the RR?" | consumes the analysis; branches never merge |

**The scale economics.** At scale, human attention is the scarce
resource. This model spends it exactly where judgment lives — novel
intent, high blast radius, new invariants, emergencies — and removes it
from where it is wasted: syntax, fan-out estimation, scheduling
arithmetic, evidence collection. Ten agents proposing changes are not ten
times the risk; they are ten times the throughput against the *same*
gates — that is the whole trick, and it only works because the gates were
built first.

## Metrics that tell you it's working

| Dimension | Metric | Healthy direction |
|---|---|---|
| Coverage | devices fully modeled; contracts with live expectations; converged-fleet % | up and to the right |
| Safety | invariant violations caught pre-merge; change failure rate; regressions caught by gates | pre-merge catches up; failures down |
| Speed | intent-to-landed lead time; % changes dispatched with no human device access | lead time down; hands-off % up |
| Honesty | % expectations `unverifiable`; evidence staleness; drift backlog age | all down — and *visible* while nonzero |

The honesty row is the one organizations skip. An `unverifiable` rate you
track is a driver-coverage roadmap; one you hide is a false sense of
green. (This lab reports its own: `core-rr-01` has no executor driver, and
its gate lines say so on every run.)

## Anti-patterns (each one observed in the wild)

- **Templating rebranded as intent.** Jinja over a spreadsheet of knob
  values stores mechanisms with extra steps. The test: can a second
  vendor render from the same source *unchanged*?
- **The parallel write path.** One tolerated 2 AM CLI session makes the
  SoT a cache of guesses. Break-glass must exist — `ibnctl`, overrides —
  and must write itself into the record.
- **Free text where machines branch.** Zone names, capability strings,
  severities — controlled vocabularies or undefined behavior.
- **Exact-value expectations.** `route_count == 173` pages on Tuesday's
  churn; `<= 200` encodes the actual promise.
- **Vendor knobs in the intent layer.** The moment a contract grows a
  `route_map_name` field, renderer independence is gone and every
  migration becomes an intent migration.
- **Big-bang adoption.** Skipping read-only stage 1 means your first
  compiled config lands on a network whose model nobody has validated.
  Converge device-by-device with the render-and-diff loop.
- **Rounding "unknown" up to "fine".** If the gate can't observe it, it
  must say `unverifiable` — silence is how monitoring lies.
- **Agents with device credentials.** The one AI rule that is never a
  dial. Agents propose intent; the executor delivers it.

---

## Exit codes & cheat sheet

| command | exit | meaning |
|---------|------|---------|
| `dispatch` | 0 | all stages applied, gates passed |
| `dispatch` | 2 | failure/abort (gate failed, step rolled back, approvals missing) |
| `dispatch` | 3 | nothing failed — waiting on a window or manual gate |

```bash
ibn-orchestrate windows [--at ISO]           # what may deploy when
ibn-orchestrate plan --candidate BRANCH      # render+diff+plan+timeline+decide
ibn-orchestrate dispatch --plan-dir plans/ID [--check|--wait|--override-windows]
ibn-orchestrate run --candidate BRANCH       # plan + dispatch
ibn-orchestrate watch [--once]               # plan on PC open, dispatch on merge
```

Design records: `autonetops_ibn/docs/adr/0013-orchestrator-timeline-and-dispatch.md`
(and 0005/0010/0011/0012 for the layer boundaries it builds on).
