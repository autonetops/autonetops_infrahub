# Intent (Intent-Based Networking)

This extension models **intent** — what the network is meant to provide — rather
than device configuration. It is the schema backbone for an IBN workflow where:

1. Intent is authored in InfraHub (branch → Proposed Change).
2. **Checks** enforce semantic invariants (no-leak, prefix authorization,
   redundancy) so bad intent never merges.
3. **Transforms/Artifacts** compile merged intent into per-platform device
   configurations, `ContractExpectations` files, and observability bundles
   (collector configs, alert rules, dashboards).

## Design principles

- **Model the contract, not the mechanism.** A `RoutingContract` says *"peer AS
  65123 may advertise only these prefixes, tagged with these communities, and
  must never reach internet peers"* — never `route-map CUSTC-IN permit 10`.
- **Make invalid states unrepresentable.** Zones, capabilities, roles,
  severities, AFI/SAFIs and actions are controlled vocabularies (Dropdowns or
  taxonomy nodes). Communities are regex-validated. ASNs and prefix limits are
  numbers, not strings.
- **Intent is stable; renderers change. Expectations are the proof.**

## Node overview

| Node | Purpose |
|---|---|
| `IntentZone` | Controlled vocabulary of security/routing zones (corp, internet-peers, transit, …) |
| `IntentCapability` | Platform capability taxonomy (gnmi, ssh_cli, mpls, …). Attached to `DcimPlatform`; compilers branch on it |
| `IntentTenant` | Unit of ownership/isolation/blast-radius; owns VRFs and a tenant marker community |
| `IntentRoutingContract` | BGP contract: prefix authorization, attribute policy, export allow/deny, attachment devices |
| `IntentInvariant` | Things that must never happen (no_leak, no_default_origination, …) |
| `IntentReachability` | Prefixes reachable from a zone, with redundancy/convergence constraints |
| `IntentSecurityPolicy` / `IntentSecurityRule` | Allowed/denied flows in business terms, attached by role selectors |
| `IntentReliability` | Guardrails: minimum PE attachments, failure-domain separation |
| `IntentObservabilitySignal` | Required signals, frequency, severity and drift condition — compiled, not bolted on |

## Capability-driven compilation

`DcimPlatform.capabilities` is the switchboard. Examples:

- A platform with `gnmi` gets a gNMI telemetry subscription generated for each
  required signal; a platform with only `ssh_cli` gets a CLI-scrape collector
  script instead — same intent, different collector.
- A renderer refuses (check fails) if an intent requires a capability the
  attachment platform does not claim.

## Dependencies

- `base` (dcim, ipam, organization, location)
- `extensions/vrf` (IpamVRF, route targets, IpamPrefix.vrf)
