# ADR 0001 — SD-WAN NAC: where validation lives, and event-driven delivery

- **Status:** Proposed (for review)
- **Date:** 2026-11-18
- **Scope:** `autonetops_infrahub` (schema, checks, webhook transform) and
  `autonetops_nac_sdwan` (GitLab CI running Cisco NAC / Terraform)

## Context

The SD-WAN data model lives in Infrahub (`schemas/sdwan.yml`, generated from the
Cisco NAC data model). The transform `sdwan_nac_export` renders it into the
`sdwan-nac` artifact (NAC YAML) for each `SdwanInstance` in the group
`sdwan_instances`. The `autonetops_nac_sdwan` GitLab project downloads that
artifact and applies it with `netascode/nac-sdwan/sdwan` (Terraform) against the
SD-WAN Manager:

```
fetch:artifact → validate:refs → validate:terraform → plan → deploy (manual, main)
```

Problems found on the first runs:

1. **`validate:refs` failed in CI.** Configuration group `edge_basic` refers to
   `edge_basic_cli`, `edge_basic_lan`, `edge_basic_system`, `edge_basic_wan` and
   `Default_Policy_Object_Profile`. None of them exist in Infrahub (0
   `SdwanCliProfile`, `SdwanSystemProfile`, ...). Root cause: those fields
   (`cli_profile`, `system_profile`, `service_profile`, `transport_profile`,
   `policy_object_profile`) are **`kind: Text`** in the schema, not relationships.
   Infrahub accepts any string, and the error only appears downstream, after the
   change has been merged.
2. **The pipeline is static.** CI hard-codes `ARTIFACT_NAME=sdwan-nac`,
   `SDWAN_INSTANCE=sdwan-manager` and `INFRAHUB_BRANCH=main`. It is started by
   hand and knows nothing about proposed changes.

Webhook events available in Infrahub 1.12.1 (`CoreStandardWebhook` /
`CoreCustomWebhook`) include `artifact.created/updated`,
`proposed_change.approved/merged/review_requested`, `branch.merged` and
`validator.passed/failed`. There is **no "proposed change opened/updated"
event**. Webhooks support `branch_scope` (`default_branch`, `other_branches`,
`all_branches`), and custom webhooks can shape the payload with a
`CoreTransformPython`.

## Decision

### D1 — Validate in Infrahub first, keep CI as a backstop

Three layers, from strongest to weakest:

| Layer | Catches | When |
|---|---|---|
| **Schema**: name references become relationships (e.g. `cli_profile → SdwanCliProfile`, cardinality one) | A reference to a missing object cannot be saved | On write |
| **Infrahub check** `sdwan_nac_integrity` (`CoreCheckDefinition`, targets `sdwan_instances`) | Rules across objects the schema can't express: router's config group in the same instance, required profiles present, unique chassis IDs, ... | Proposed change, blocks merge |
| **CI** `validate:refs` (kept, minimal) | Data that reached `main` without a proposed change; old artifacts | Before `plan` |

The schema change belongs in the **schema generator**: map NAC "name reference"
fields to relationships. Start with configuration-group profiles and router
`configuration_group`. The transform outputs the peer's `name`, so the NAC YAML
does not change.

### D2 — Trigger GitLab from Infrahub on `artifact.updated`

Use `infrahub.artifact.updated` (plus `artifact.created` for the first render of
a branch) rather than a proposed-change event:

- It fires only when the **SD-WAN render actually changed**. Proposed changes
  that don't touch SD-WAN data don't start the pipeline.
- It fires **after** the render, so CI never fetches an outdated artifact.
- It works the same way for proposed-change branches and for `main`.

```
 ┌─ Branch / Proposed Change ───────────────────────────────────────────┐
 │ edit SD-WAN data → PC pipeline runs checks + regenerates sdwan-nac   │
 │        └─ artifact.updated  (branch_scope: other_branches)           │
 │              └─► GitLab: fetch → validate → PLAN (no deploy)         │
 │                    └─ plan summary posted back as a PC comment       │
 └──────────────────────────────────────────────────────────────────────┘
 ┌─ Merge ──────────────────────────────────────────────────────────────┐
 │ PC merged → sdwan-nac regenerated on main                            │
 │        └─ artifact.updated  (branch_scope: default_branch)           │
 │              └─► GitLab: fetch → validate → plan → DEPLOY (manual)   │
 └──────────────────────────────────────────────────────────────────────┘
```

Implementation:

- **`CoreCustomWebhook`** with the Python transform `sdwan_gitlab_trigger`
  (in this repo). It ignores artifacts other than `sdwan-nac` and builds the
  GitLab trigger body:
  ```json
  { "ref": "main",
    "variables": { "ARTIFACT_ID": "...", "INFRAHUB_BRANCH": "...",
                   "SDWAN_INSTANCE": "sdwan-manager", "PROPOSED_CHANGE_ID": "..." } }
  ```
- **URL:** `https://gitlab.com/api/v4/projects/<id>/trigger/pipeline?token=<trigger-token>`.
  The token is stored in the webhook object in Infrahub, never in Git.
- **Branch rule in CI:** `deploy` exists only when `INFRAHUB_BRANCH == main`.
  Proposed-change branches only get `plan`.
- **Feedback (phase 2):** `plan` posts a summary (adds / changes / destroys,
  config groups touched, devices to be redeployed) as a thread on the proposed
  change through GraphQL, so reviewers see the impact on the network next to the
  data diff.

### D3 — CI gets its inputs from the event, not hard-coded values

| Today | Event-driven |
|---|---|
| `ARTIFACT_NAME` + `SDWAN_INSTANCE` → GraphQL search | `ARTIFACT_ID` from the event; fetch by ID, still check `Ready` and checksum |
| `INFRAHUB_BRANCH=main` | From the event |
| One state | `TF_STATE_NAME=$SDWAN_INSTANCE` (already in place) |
| `SDWAN_URL` / credentials as global variables | GitLab variables **scoped to environment** `sdwan/<instance>`, one set per Manager; Manager URL optionally stored on `SdwanInstance` |

Manual and web-triggered runs keep the current defaults as a fallback.
`SDWAN_INSTANCE` is passed in the payload rather than worked out inside CI,
because `resource_group` and `TF_STATE_NAME` are fixed when the pipeline is
created.

## Consequences

**Positive**
- Broken references are blocked at write time or merge time, not found after merge.
- Every SD-WAN proposed change gets a real `terraform plan` against the Manager
  before merge; deploys only come from `main`.
- One pipeline serves any number of `SdwanInstance`s with no CI edits.

**Negative / costs**
- Schema generator work, and a migration from Text to relationships for existing data.
- A webhook transform and trigger token to maintain; the token must be rotated in Infrahub.
- Plans on proposed-change branches take the Terraform state lock; the shared
  `resource_group` serializes them with deploys, so queuing is possible.

## Open questions to verify during implementation

1. Does a `CoreCustomWebhook` transform in 1.12 receive the event payload
   directly, and can it use `self.client` to look up the artifact's target
   (instance name) and the related proposed change?
2. Does `artifact.updated` fire on the first render in a new branch, or only
   `artifact.created`? (Plan: subscribe to both.)
3. Brownfield: `edge_basic` (and its profiles) already exist on the Manager. On
   the first apply NAC would try to create them and collide. Decide between
   `terraform import` and new names managed only by NAC.
4. Device impact: the module defaults `configuration_group_deploy: true`, so
   every router under `sites[].routers` is redeployed on apply. Decide whether to
   override it per site or keep the manual gate as the only control.

## Implementation order

1. Infrahub check `sdwan_nac_integrity` (fast, blocks bad merges now).
2. Custom webhook + `sdwan_gitlab_trigger` transform; CI reads `ARTIFACT_ID` / `INFRAHUB_BRANCH`.
3. CI branch rule: plan-only for proposed-change branches, deploy on `main`.
4. Post the plan back to the proposed change.
5. Schema generator: name references → relationships.
