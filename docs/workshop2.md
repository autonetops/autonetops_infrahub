# Workshop 2 — Intent-Based Networking: the theory, the operation, the road to scale

Workshop 1 built the pipeline: intent in InfraHub, compiled to configs,
expectations and telemetry, gated by invariants. This workshop finishes the
picture in three parts:

- **[Part I — Theory](#part-i--theory-what-intent-based-networking-actually-is)**
  defines intent-based networking properly: the concepts, the precise
  terminology (intent, contract, objective, policy, invariant, waiver,
  expectation, …) and the relationships between them — the **shared
  vocabulary** of this lab and the companion platform repo
  (`autonetops_ibn`, ADR-0015/0017) — stated generally enough to fit most
  networks, then anchored to the exact InfraHub kinds this lab implements
  (`schemas/intent.yml` mirrors the platform repo's `intent_model.yml`;
  §4's migration ledger records how the lab got there).
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
   (*InfraHub: `IntentDefinition` and the instruments attached to it,
   edited on branches.*)
2. **Constraints that must never break are first-class objects** that gate
   every change before it merges.
   (*Policies + invariant projections → merge-blocking checks on Proposed
   Changes; a reviewed, expiring waiver is the only way past one.*)
3. **Mechanisms are compiled, never hand-written.** Vendor syntax, session
   lists, filters — all renderer output, regenerated at will.
   (*Transforms: contracts → EOS / SR Linux / IOS-XE / FRR configs.*)
4. **Every promise carries a proof obligation** stated in observable terms,
   with the collection method derived from what each platform can do.
   (*The `contract-expectations` artifact; capability-selected collectors.*)
5. **Delivery is planned, scheduled and gated by data** — blast radius is
   computed, canary strategy is reviewed policy, change windows live in the
   SoT, and rollout gates evaluate the same expectations as steady-state
   monitoring. All of it is **operational policy**: reviewed rules that
   constrain the automation itself.
   (*`OpsChangeWindow`, the planner's Plan, the validator's verdicts.*)
6. **The loop closes.** Observed state is continuously compared with
   declared state; divergence is drift, and drift is either reconciled or
   escalated with evidence — never silently absorbed.
   (*Prometheus alerts on expectations; the reconcile/watch loop.*)

The composition model fits in one breath, and the rest of Part I unpacks
it:

> An intent is **achieved by its contracts**, **governed by policies**,
> **guaranteed by invariants**, and **tempered by waivers**.

A useful mnemonic: every question the model answers is a distinct kind of
object, and keeping them separate is most of the discipline:

| Question | Concept | Kind |
|---|---|---|
| **WHY / WHAT** outcome must hold? | Intent (the aggregate root) | `IntentDefinition` |
| **WHO** is it for? | Tenant | `IntentTenant` |
| **WHERE** does it apply? | Zone | `IntentZone` |
| **HOW** is it achieved? | Contract → objectives | `IntentContract` subtypes |
| **WHAT ARE THE RULES** of the game? | Policy (network + operational) | `IntentPolicy` rule instruments; `OpsChangeWindow` |
| **WHAT MUST NEVER BE FALSE?** | Invariant | `IntentInvariant` |
| **WITH WHAT** can each box participate? | Capability | `IntentCapability` (on `DcimPlatform`) |
| **WHEN** may change reach production? | Change window (an operational policy) | `OpsChangeWindow` |
| **PROOF** that it holds? | Expectation → evidence | `contract-expectations` artifact → telemetry |
| **SANCTIONED EXCEPTIONS?** | Waiver (time-boxed, expiring) | `IntentWaiver` |

(WHEN, PROOF and the waiver row are the three that classic "source of
truth" projects forget — and they are exactly what makes automation
trustworthy at scale.)

## 3. The control loop

Everything in this repo and its companion is one loop. Every phase of
Part II is one arrow of it:

```
            ┌──────────────────────────────────────────────────────────┐
            │                    SOURCE OF TRUTH                       │
            │   intent — achieved by contracts, governed by policies,  │
            │            guaranteed by invariants, tempered by waivers │
            │   + tenants, zones, capabilities, change windows         │
            └──────────────────────────────────────────────────────────┘
 DECLARE     branch → edit intent → Proposed Change     humans and AI agents,
                                                        same write path
 GATE        policies + invariant projections run as    design-time enforcement;
             checks on the PC; a major/critical         only a reviewed, expiring
             violation cannot merge                     waiver gets past one
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

## 4. Terminology — the shared vocabulary

The words below are used precisely throughout this repo **and its
companion** — they are one vocabulary, negotiated term by term. ADR-0015
(in `autonetops_ibn`) defines the model; ADR-0017 records the alignment
with this workshop's original ontology, best definition winning whichever
repo it came from. Each entry gives the definition, where it lives, and —
just as important — what it is *not*, because most failed IBN projects die
of blurred vocabulary. The lab schema implements this model as written;
the [migration ledger](#the-migration-ledger--one-vocabulary-two-repos) at
the end of this section records the renames that got it there from the
workshop's original ontology.

### Intent

A **declared outcome the network must achieve** — the aggregate root of
the model: identity, ownership, urgency. It compiles to *nothing*; the
instruments attached to it do all the work. (*Kind: `IntentDefinition` —
namespace `Intent`, name `Definition`. Fields: `name`; `description` —
the outcome in one human sentence, stated as WHY, never as mechanism;
`domain`; `owner`; `ticket`; `priority` critical/high/medium/low;
`status` draft/active/deprecated/retired — deliberately no `suspended`:
an un-expiring off-switch is exactly the failure mode
[waivers](#waiver) exist to prevent.*)

The test for a well-formed intent: **it survives a vendor swap and a
redesign unchanged.** "customer-c has resilient L3VPN connectivity between
its sites" is an intent. "configure `local-preference 150` on pe-emea-01"
is not — that is a mechanism wearing an intent's clothes, and it belongs
in renderer output. This lab re-platformed its route reflector twice
(vJunos → SR Linux → Cisco IOL) with **zero** intent edits; that is the
property you are buying.

Intent `priority` is business importance, and it *travels*: in Part II the
plan inherits `critical` from the intents whose contracts sit on the
touched devices. Priority is declared once, at the root, and only flows
*down* (intent → contract → plan); every downstream decision (emergency
eligibility, approval requirements) reads it instead of re-asking, and
nothing below the intent may raise its own.

### Domain

A **domain of concern an intent is declared in** — an *attribute on the
intent, not a node kind*, so the taxonomy grows without a schema
migration. Starter set: `connectivity`, `routing`, `observability`,
`security`, `resilience`, `capacity`, `compliance`, `lifecycle`,
`operations`.

Domains are the most stable layer of the whole model — almost every
network, from a campus to a hyperscaler, needs roughly this same set.
They exist to keep intents honest about what they are promising, and to
give policies and invariants a scope to attach by (`scope: domain`). A
domain is **not** an org chart or a team; it is a category of promise.

(*The workshop's earlier ontology modeled this as an `IntentRealm` node
kind — retired by the alignment, with three renames worth
learning: `reliability → resilience` (the property, not the wish),
`performance → capacity` (utilization ceilings, latency budgets, QoS
promises), and `reachability` is not a domain at all — it is an
**objective kind** inside contracts: "site A reaches site B" belongs to
the same intent as the sessions that make it true, and promoting it to a
domain would scatter one intent's terms across two intents.*)

### Contract

A **typed, machine-renderable promise of a measurable outcome to a named
consumer** — a customer, a peer, an internal team. The classifying
question: *who loses if this breaks?* A contract fails as a **broken
promise**. (*Generic: `IntentContract`; subtypes per domain:
`IntentRoutingContract`, `IntentReachabilityContract`,
`IntentSecurityContract`, `IntentObservabilityContract`,
`IntentReliabilityContract`.*)

Contracts belong to **exactly one intent** — owned, cascade-deleted,
never shared — and they *are* the realization of it, which makes them the
**swap point**: when you change *how* — different attachment design,
different protocol, different vendor approach — you retire contracts and
attach new ones; the intent above them does not move. An intent with zero
contracts is untestable furniture — nothing about it can ever fail — and
that is a merge-check finding, not a style opinion.

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
   obligations (sessions established, route counts within bounds).
3. **Anchor to reality** — contracts reference real DCIM/IPAM objects
   (devices, prefixes, zones), so compilers know where artifacts land and
   checks know what universe to validate against.

A contract is **not** a config template (templates have vendor syntax) and
**not** an invariant (a contract says what *should* be, for someone; an
invariant says what must *never* be false, for everyone).

(*The earlier lab schema reached the intent through a grouping hop —
`contract → policy → intent`. The alignment flattened it: contracts carry
a direct `intent` parent, and the freed word "policy" names what the
[Policy](#policy) entry defines.*)

### Objective

**One measurable term inside a contract** — the SRE analogue is the SLO.
"Session established", "≤ 200 IPv4 prefixes", "p99 RTT < 30 ms" are
objectives; the contract bundles them into one promise to one consumer.
Expectations are compiled **per objective**, and verdicts roll up the
other way: objective → contract → intent, worst-of. (*Kind:
`IntentObjective`, owned by its contract.*)

Typed contract kinds encode their objectives as native attributes — the
lab routing contract's `max_prefixes_ipv4` and `afi_safis` *are* its
objectives, machine-readable by construction. Free-form contracts carry
explicit objective nodes instead. Both answer the same platform queries,
because the queries target the generic.

### Policy

A **standing rule constraining how outcomes may be realized**. The
classifying question: *which actions or designs does it forbid or shape?*
A policy fails as a **rejected change** — nothing broke; the gate said
no. Two subjects, same shape:

- **Network policies** constrain the network's design and configuration:
  "every eBGP session carries max-prefix", "no public addressing on
  management", "communities from zone X are forbidden toward Y".
- **Operational policies** constrain the *automation itself*: change
  windows and freezes (`OpsChangeWindow`), canary strategies and their
  approval bars, pre-approved change classes, rate limits, delivery
  preferences.

That one word covers both is a feature: both are reviewed data, both bite
at decision time, both answer "what are the rules of the game?" — only
the subject differs. And *where* a policy bites (merge, plan compile,
dispatch) is **derived from what it is**, never authored: the lab's
retired `enforcement: design_time | runtime | full` dropdown could
contradict the attached instruments; a derived fact cannot.

A policy is **not** a "routing policy" in the vendor sense (route-maps
are compiled *from* contracts), and — the big unlearning if you drafted
against the old lab schema — **not a bundle of contracts**. Policies are
rules: referenced by scope (`global | domain | intent`), never owned by
an intent, never deleted with one. (*Kind: `IntentPolicy`, a generic —
`IntentGenericPolicy` is the free-form starter (the lab seeds
`ebgp-max-prefix-required`), and `OpsChangeWindow` inherits it with
`subject: operational`. The earlier lab kind of the same name was the
grouping node; the alignment retired it.*)

### Invariant

A **condition on network state that must always hold — independent of any
contract, and of whatever changed recently**. The classifying question:
*if this is false, is it an incident regardless of what merged?* An
invariant fails as an **incident**. (*Kind: `IntentInvariant`, a generic;
the type IS the kind — `IntentNoLeakInvariant`,
`IntentNoDefaultOriginationInvariant`,
`IntentPrefixAuthorizationInvariant`, `IntentNoReoriginationInvariant`,
`IntentRedundancyInvariant`, `IntentCapabilityPresentInvariant`,
`IntentReachabilityPreservedInvariant`, `IntentOobReachabilityInvariant`
— each inheriting the generic. Invariants are referenced by scope
(`global | domain | intent`): `fleet-oob-cabled` is global; the no-leak
invariant references the customer intent it guarantees.*)

One authored invariant is enforced on **two surfaces**:

- **Continuously, against the live network** — its compiled, always-on
  [expectations](#expectation). `custc-no-leak` is violated the moment
  customer-c routes reach the peering zone, whoever's change (or no
  change at all) caused it.
- **Projected onto merge time** — a derived check asking "would this
  change put the SoT in a state that violates the invariant?". In
  workshop 1 you watched this surface: add customer-c to a peering
  contract's `export_tenants` and the Proposed Change cannot merge, with
  a message naming the tenant, the contract and the denied zone.

`severity` — `warning | major | critical` — states what a violation is
*worth when it happens*, and enforcement derives from it: `major` and
`critical` merge projections **block**, `warning` projections report;
live violations page at the severity's weight. (*The workshop's earlier
`blocking | warning` enum was an enforcement consequence, not a worth; it
mapped losslessly — `blocking → major` (or `critical` where a live
violation pages), `warning → warning` — and "blocking" survives as a
property of the derived check, which is where it always really lived.*)

Crucially, invariants are **declared data, not tribal rules**. "We never
announce customer space to peers" as folklore dies with staff turnover; as
a declared invariant it outlives everyone and applies equally to the new
hire and the AI agent.

### Waiver

A **reviewed, owned, time-boxed permission for one subject to violate one
policy or invariant**. One target (the policy or invariant being waived),
one subject (an intent, a contract, or a device/site scope), a named
owner, and a **hard expiry** — past it, the violation is a finding again.
Governance is built into the shape: a waiver is *requested* by the
subject's owner and *approved* by the target's owner — you don't waive
your own rules for yourself. (*Kind: `IntentWaiver` — one
`waived_policy` **xor** `waived_invariant` target, a subject, and a
DateTime `expires_at` consumers must check before honoring it.*)

Waivers are the missing piece once invariants gate merges for real: every
network carries sanctioned violations — the legacy box that cannot do
max-prefix, the single-homed site whose second circuit is on order — and
without a waiver object each one becomes an unmergeable branch or an
eternal red light everyone learns to ignore. Waived findings surface as
`waived` on every report: a third state that is never green and never
red. The rejected synonym is "exception" — rejected because it invites
permanence, and **no waiver is permanent**.

### Expectation

The **runtime proof obligation** compiled from intent: what must be
observable, via which collector, how fresh, for the promise to count as
held. Expectations are the **only check form the runtime evaluates**:
contracts compile to them per objective (sessions established, route
counts within bounds), and invariants compile to always-on expectations
with fleet scope (forbidden exports absent) — so a live no-leak finding
cites the invariant, not a contract that happens to mention zones.
(*Artifact: `contract-expectations`, one per contract, versioned and
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

### Invariant projection vs expectation (the distinction that carries the model)

Both compile from the same authored intent, but they are enforcement on
different surfaces, and conflating them is the most common modeling
error:

| | **Invariant projection** | **Expectation** |
|---|---|---|
| Evaluated over | the intent graph (the SoT itself) | the live network (telemetry) |
| Evaluated when | at review time, on every Proposed Change | continuously, and at rollout gates |
| Catches | a bad *change* before it merges | the *world* diverging after merge |
| Verdict acts by | blocking the merge (`major`/`critical`) or reporting (`warning`) — unless a live waiver covers it | failing a gate / firing an alert |
| Example | "this prefix is outside the tenant's space → no merge" | "this session is not `established` → page" |

A projection stops you from *declaring* a broken world. An expectation
detects the world *becoming* broken on its own. You need both — and they
are two surfaces of **one authored object**, not two objects: a network
with only merge projections is blind after merge; a network with only
expectations merges its own outages and then alerts on them.

### Tenant, Zone, Capability

The supporting vocabulary — each exists so that a critical question is
answered by a typed relationship instead of a naming convention (this
trio came from the workshop side of the alignment and was adopted into
the platform model unchanged):

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

**WHEN, as reviewed data in the SoT — the operational policy you meet
first.** (*Kind: `OpsChangeWindow`, `schemas/operations.yml`.* Two
shapes: recurring — `days`, `start_utc`, `duration_minutes`; absolute —
`starts_at`, `ends_at`. `window_type` is `maintenance` (deployment
allowed inside) or `freeze` (deployment of matching devices blocked
inside, whatever other windows say). Each window carries a
`max_blast_radius` ceiling and a device scope; empty scope =
fleet-wide.)

The intent model says WHAT must hold and HOW it materializes; windows say
WHEN a plan may touch production. Putting them in the SoT — versioned,
reviewed, queried — instead of a YAML file on an operator's laptop means
the scheduler's inputs have the same provenance as the intent itself.
And it is schema, not aspiration: `OpsChangeWindow` inherits `IntentPolicy`
(`subject: operational`), so "what rules are in force" is one query
across network and operational policies alike.

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

(Compliance reports add a fifth state on top of these: `waived` — a
finding covered by a live [waiver](#waiver); visible, never green, never
red, until expiry flips it back.)

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
| **priority** | Intent (only; flows down) | critical / high / medium / low | How important is this outcome to the business? Inherited by plans; gates emergency eligibility. |
| **severity** | Invariant (authored) | warning / major / critical — the paging scale in compiled alerting blocks (`sev1`…`sev4`) is renderer output *derived* from it | How bad is a violation of this specific condition when it happens? Merge projections derive their blocking behavior from it. |
| **blast radius** | Plan (computed); window (ceiling) | low / moderate / high | How big is this specific change? Selects the canary strategy and the admitting windows. |

A `critical`-priority intent can receive a `low`-blast-radius change
(tweak one value) guarded by a `critical`-severity invariant. Three
different questions, three different objects.

### The relationship map

Cardinalities as implemented in `schemas/intent.yml` (mirroring the
platform repo's `infrahub/schemas/intent_model.yml`, per ADR-0015/0017):

```
IntentTenant (WHO)     IntentZone (WHERE — referenced     IntentCapability (WITH WHAT —
      │ 0..1            by contracts and invariants)       attached to DcimPlatform)
      ▼
* IntentDefinition     WHY: name, description, domain, owner,
      │                priority, status  (the WHAT; survives redesigns)
      │
      │ ACHIEVED BY                GOVERNED BY               GUARANTEED BY
      │ (owned, cascade)           (referenced, never owned — attach by
      ▼                             scope: global | domain | intent)
* IntentContract               * IntentPolicy            * IntentInvariant
   (typed promise —              (rules of the game:       (must never be false;
    feeds the compilers)          network | operational)    severity: warning |
      │ owns (cascade)                 ▲                    major | critical)
      ▼                                │ target                  ▲ target
* IntentObjective                      └──── IntentWaiver ───────┘
   (one measurable term;                (one target, one subject, an owner,
    expectations compile                 a HARD expiry; subject ➞ intent |
    per objective)                       contract | device scope)

contract anchors:  * DcimGenericDevice  (pe_devices / ce_devices)
                   * IpamPrefix         (allowed_prefixes, to_prefixes)
                   1 IntentZone         (+ export_deny_zones)
                   * IntentTenant       (export_tenants)
                   watched by * IntentObservabilitySignal (condition, frequency)

DcimPlatform ── * IntentCapability      selects collectors, gates renderer
                                        arms, checked by capability_present
OpsChangeWindow ── * DcimGenericDevice  WHEN — an operational policy;
                                        maintenance | freeze; max_blast_radius;
                                        empty scope = fleet
```

Derived from that graph, never stored in it: device configs,
expectations, collector configs, alert rules, dashboards, plans, evidence.
If you can regenerate it, it is not intent.

### The migration ledger — one vocabulary, two repos

The vocabulary above is shared property, and ADR-0017 records the deal.
The lab schema (`schemas/intent.yml`) predated it under the workshop's
original ontology; these renames — each a rename, not a redesign —
brought it into line (item 8, the alerting-severity derivation, remains
the one owed item):

| # | Before (workshop ontology) | After (implemented) |
|---|---|---|
| 1 | `IntentIntent`, with `statement` and a `suspended` status | `IntentDefinition`; `statement` folds into `description`; `suspended` retires in favor of waivers (or `deprecated`) |
| 2 | `IntentRealm` node kind; realms `reliability`, `performance`, `reachability` | `domain` attribute on the intent; `resilience`, `capacity`; reachability becomes contract objectives |
| 3 | `contract → IntentPolicy (grouping) → intent`, `enforcement` field | contracts link straight to their intent; grouping node retires; "policy" names rule instruments; enforcement is derived |
| 4 | invariant `severity: blocking \| warning` | `warning \| major \| critical`; "blocking" is derived on the merge-time check |
| 5 | `invariant_type` enum on one kind | typed invariant kinds inheriting `IntentInvariant` |
| 6 | *(absent)* | `IntentWaiver` and `IntentObjective` adopted |
| 7 | `OpsChangeWindow` standalone | gains `inherit_from: [IntentPolicy]`, `subject: operational` |
| 8 | signal severities `sev1`…`sev4` authored per signal | paging scale becomes compiled output, derived from authored severity |

Item 3 landed together with the platform repo's `priority.py`
hop-shortening — the planner walks `contract → intent` directly now; the
two merges were one change window, exactly as ADR-0017 required. The
migration itself is repeatable data + schema surgery:
`scripts/migrate_to_aligned_model.py` (purge), the
`schemas/migrations/retire_*.yml` loads, then `schemas/intent.yml` +
`schemas/operations.yml` and a re-bootstrap — the full sequence is in
`retire_1_detach.yml`'s header. Part II's hands-on flow only ever
touches **contracts and windows**, which meant the same thing in both
vocabularies all along.

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
   cannot block a merge, survive turnover, or bind an AI agent — and an
   unwritten exception cannot expire. Rules are policies and invariants;
   exceptions are waivers.
6. **Everything operational is reviewed data.** Change windows, canary
   strategies, priorities — operational policies in the SoT or in
   reviewed policy files, never in someone's terminal history.
7. **One write path for humans and machines.** Branch → review → merge →
   compile → plan → dispatch. Break-glass exists (`ibnctl`,
   `--override-windows`, `--emergency`) but goes on the record.
8. **Verdicts are honest.** pass / fail / pre_existing / unverifiable —
   and waived findings say `waived`, never green. A gate that can't
   observe something says so; it never rounds "unknown" up to "fine".
   Rate limits report `not_evaluated` until an evaluator exists.
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
*model* is not SP-specific — the object grammar stays fixed; what changes
per environment is your domain taxonomy, which contract subtypes you
write and which invariants matter most:

| Environment | Typical contracts | Typical invariants (major/critical) | Typical expectations |
|---|---|---|---|
| **SP / WAN** (this lab) | routing (CE↔PE, core, peering), reachability objectives | no_leak, prefix_authorization, redundancy | sessions established, route counts bounded, forbidden exports absent |
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
  customer space to peers" recited in every design review is an
  invariant waiting to be an object.
- **Model the exceptions people apologize for.** "That box can't do
  max-prefix, we know" recited in every audit is a waiver waiting to be
  an object — with an expiry.
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

*Concepts in play: [change window](#change-window) (an operational
[policy](#policy)), [blast radius](#blast-radius) as a window ceiling,
design law 6 (everything operational is reviewed data).*

`schemas/operations.yml` adds one kind, `OpsChangeWindow`. Windows are
operational policy: reviewed, versioned, queried — never a YAML file on
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

In the UI: **Intent → Policies → Change Windows** (windows are
operational policies, and the sidebar mirrors the model). Via GraphQL:

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
the one write path (design law 7), [invariant](#invariant) projections on
the branch.*

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
3. **Intent → Contracts → custc-ce-to-pe**: edit `local_preference` to `150`;
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
relationship delta. The invariant projections run on the branch too:

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
  strategy from `planner/policies/canary_strategies.yaml` (an operational
  policy file; built-ins while it is a draft): one target first, 30-minute
  soak, then the rest.
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
   `--approve platform-lead` — exiting `4` (an approvals hold: nothing
   ran, nothing broke, and `watch` holds the plan instead of dead-ending
   it). The approval lands in the decision record.
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
the OOB plane), IP space, VRFs, locations. Model only what the first
domain you pick will need — a complete model of everything is a project
that never ships.

**The keystone habit.** Flip the direction of record: the SoT stops being
where reality is *documented afterwards* and becomes where it is
*declared first*. A device exists because it was created in the SoT and
then racked, not the other way around.

**Exit criteria.** New devices enter through the SoT; platform
capabilities are data (`gnmi`, `ssh_cli`, `snmp`, `mpls`, …); cabling for
the target domain is modeled. You can answer "what is attached to what,
and what can each box do?" with a query.

## Stage 1 — Intent, read-only: expectations before automation

**The work.** Pick **one domain and one service** you already promise
(here: connectivity + customer-c's L3VPN). Model the chain — intent →
contracts, with the policies and invariants that govern them — and
compile **only** expectations, collectors, alerts and dashboards. Push no
configs. The network keeps being managed however it is managed today.

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
merge-gating checks. Start with the rules people recite in design
reviews: prefix authorization, no-leak, minimum redundancy, capability
presence. Give sanctioned violations a **waiver** from day one — one
target, one subject, a hard expiry — or your first real gate becomes
your first permanent override.

**The payoff.** Each blocked merge is an outage that didn't happen, with
a named invariant and a message explaining itself. This is also the stage
where review changes character: reviewers approve *intent deltas* ("new
range for customer-c, local-pref 150") instead of eyeballing config
diffs.

**Exit criteria.** ≥90% of changes to the modeled domain enter via PC;
every `major`/`critical` invariant has a merge-time check; violations
caught pre-merge is a number you report; zero un-expiring exceptions.

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
reviewed operational-policy files, dispatch with snapshot → dry_run →
apply → rollback, gates evaluating expectations per stage, a baseline
separating regressions from pre-existing drift, evidence appended per
verb.

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
   all-checks-green changes; waiver approvals, invariant overrides and
   emergency dispatches remain human forever.
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
intent, high blast radius, new invariants, waivers, emergencies — and
removes it from where it is wasted: syntax, fan-out estimation,
scheduling arithmetic, evidence collection. Ten agents proposing changes
are not ten times the risk; they are ten times the throughput against the
*same* gates — that is the whole trick, and it only works because the
gates were built first.

## Metrics that tell you it's working

| Dimension | Metric | Healthy direction |
|---|---|---|
| Coverage | devices fully modeled; contracts with live expectations; converged-fleet % | up and to the right |
| Safety | invariant violations caught pre-merge; change failure rate; regressions caught by gates | pre-merge catches up; failures down |
| Speed | intent-to-landed lead time; % changes dispatched with no human device access | lead time down; hands-off % up |
| Honesty | % expectations `unverifiable`; evidence staleness; drift backlog age; waivers past expiry | all down — and *visible* while nonzero |

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
- **The permanent exception.** A disabled invariant or a suspended intent
  is a violation nobody is counting. Sanctioned violations get a waiver —
  one target, one subject, an owner, a hard expiry — and show as `waived`
  (never green, never red) until they become findings again on schedule.
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
| `dispatch` | 2 | failure/abort (gate failed, step rolled back) |
| `dispatch` | 3 | nothing failed — waiting on a window or manual gate |
| `dispatch` | 4 | nothing ran — approvals missing or a policy hold; `watch` holds the plan |

```bash
ibn-orchestrate windows [--at ISO]           # what may deploy when
ibn-orchestrate plan --candidate BRANCH      # render+diff+plan+timeline+decide
ibn-orchestrate dispatch --plan-dir plans/ID [--check|--wait|--override-windows]
ibn-orchestrate run --candidate BRANCH       # plan + dispatch
ibn-orchestrate watch [--once]               # plan on PC open, dispatch on merge
```

Design records, all in `autonetops_ibn/docs/adr/`:
`0013-orchestrator-timeline-and-dispatch.md` for the machinery of Part II
(with 0005/0010/0011/0012 for the layer boundaries it builds on, and 0014
for pre-approved plan classes); `0015-intent-vocabulary.md` and
`0017-vocabulary-alignment-lab-ontology.md` for the shared vocabulary
Part I teaches (the full model: `docs/architecture/intent-model.md` and
`infrahub/schemas/intent_model.yml` in that repo).
