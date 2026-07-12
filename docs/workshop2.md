# Workshop 2 — Change the intent, let the orchestrator land it

Workshop 1 built the pipeline: intent in InfraHub, compiled to configs,
expectations and telemetry, gated by invariants. This workshop is about
**operating** it: you change the SoT — interfaces, routing policy,
advertisements — and the orchestrator does the rest:

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
