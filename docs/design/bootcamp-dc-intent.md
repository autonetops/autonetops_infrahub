# Bootcamp DC1/DC2 — intent design (draft)

- **Status:** DRAFT — nothing in this document is in Infrahub yet. Review, answer
  the open questions (§9), then load intents on a branch.
- **Date:** 2026-11-22
- **Scope:** sites `bootcamp_dc1` and `bootcamp_dc2` (namespace `bootcamp_dc`), AS 20.
- **Model:** the IBN vocabulary of this repo (`schemas/intent.yml`,
  `docs/workshop2.md` §4–5, ADR-0002). An intent is **achieved by** contracts,
  **governed by** policies, **guaranteed by** invariants; contracts compile to
  configs + expectations + signals.
- **Evidence:** read-only `show running-config` and protocol state collected over
  SSH on 2026-11-22 from all 13 IOS devices (10.10.10.110, .113–.118, .124–.129),
  plus the Infrahub `main` inventory. No device was changed. Secrets (PSKs, BGP/LDP/
  OSPF keys, HSRP auth, SNMP communities) are deliberately **not** reproduced here.

How to read it: §2 is what exists, §3 the intent vocabulary for this network,
§4 the intent statements, §5–7 what each statement derives into (configs,
expectations, signals), §8 the gaps found while deriving, §9 what I need from you.

---

## 1. One-paragraph summary

Two data centres run one MPLS L3VPN provider core (AS 20). Each DC has a pair of
route reflectors that are **also** the gateway pair for the shared-services VRF
`s100` (HSRP on Vlan100, firewall behind it), a pair of `PE-LL3` routers for
directly-attached partners, one `PE-VPN` router that terminates partner
IPsec+eBGP tunnels, and an L2 edge switch whose HSRP VIP is the PE-VPN default
gateway. The two DCs are joined by RR↔RR links (OSPF/LDP/iBGP) and by an L2
port-channel between the edge switches. Partners (`v146`, `v183`, `v211`, `v300`)
are isolated in their own VRFs; their routes are tagged RT 20:199 and leaked into
`s100`; partners receive only the public F5 VIP ranges (145.245.114.0/24,
145.245.115.0/24).

## 2. Topology as observed (2026-11-22)

### 2.1 Roles and devices

| Role | DC1 | DC2 | Platform |
|---|---|---|---|
| Route reflector + s100 gateway | DC01-RR-01 (.116), DC01-RR-02 (.115) | DC02-RR-01 (.126), DC02-RR-02 (.125) | IOSvL2 |
| PE, direct-attached partners | DC01-PE-LL3-01 (.117), -02 (.118) | DC02-PE-LL3-01 (.127), -02 (.128) | IOSv |
| PE, IPsec partners | DC01-PE-VPN-L2-01 (.114) | DC02-PE-VPN-L2-01 (.124) | C8000V (IOS XE 17.15) |
| Edge L2 switch | DC01-SW-LL2-01 (.110) | DC02-SW-LL2-01 (.129) | IOL L2 |
| Distribution (F5 VIP routing) | DC01-DIST (.113) | *not onboarded* (pinged at 10.245.129.9) | IOL L2 |
| Firewall | DC01-FW (.112, PA-VM) | DC02-FW (.122) | PAN-OS |
| Load balancer | *LB-DC1 not onboarded* | LB-DC2 (.121) | F5 BIG-IP |

Management: every device has VRF `oob`, 10.10.10.0/24, default via 10.10.10.1.

### 2.2 Addressing plan (as found)

| Block | DC1 | DC2 |
|---|---|---|
| Loopback0 (router-id, RD base) | 10.245.245.0/24 (.246 PE-VPN, .251/.252 LL3, .253/.254 RR) | 10.245.246.0/24 (same host numbering) |
| Core p2p /30 | 10.245.137.0/24, 10.245.138.0/24 | 10.245.141.0/24, 10.245.142.0/24 |
| RR↔RR in-DC (Vlan99) | 10.245.137.0/30 | 10.245.141.0/30 |
| DCI RR↔RR | 10.245.137.4/30 (RR-01s), 10.245.137.8/30 (RR-02s) | — |
| s100 gateway (Vlan100, HSRP VIP .17, FW .22) | 10.245.136.16/29 | 10.245.140.16/29 |
| FW↔DIST (vlan 601) | 10.245.128.8/29 | 10.245.129.8/29 |
| F5 VIP ranges behind DIST | 10.245.144–147.0/24 | ? |
| Internet edge (SW Vlan1, PE-VPN Gi1) | 130.221.251.48/28 shared across DCs (SW VIP .49, PE-VPN VIP .62) | same L2 segment |
| Partner underlay | 100.120.0.0/24 (SW Et0/3, VIP .2; partner endpoint 100.120.0.1) | same segment |
| Partner tunnel /30s | 192.168.28.192/30 (v300) | same /30, standby copy |
| LL3 partner subnets | 172.31.91.8/29 (v183, VIP .9) | 172.32.91.8/29 (v183, VIP .9) |

### 2.3 Control plane

- **IGP:** OSPF 1, area 0, MD5 auth, p2p links, SPF/LSA throttling, `redistribute static`.
- **Labels:** LDP with per-neighbor passwords, `igp sync`, labels advertised only for
  loopbacks (`bb-loopbacks`).
- **iBGP:** AS 20, VPNv4 only, timers 8/24. Each PE peers with **its own DC's** two
  RRs; the 4 RRs form a full mesh (`EDGE-IBGP-MESH`); PEs are `EDGE-RR-CLIENT`.
- **VRFs:**
  - `s100` (shared services): RRs export RT 20:100, import 20:199; PEs import 20:100.
  - Partner VRFs `v<N>`: import RT 20:100; `export map export-s100-shared` adds
    RT 20:199 to everything except RFC1918/bogons (`private`), with the
    `private-s100` exception list allowed.
  - `oob`: management only.
- **Shared-services reachability:** RRs hold static routes in `s100` (10/8,
  145.245.114/24, 145.245.115/24, vlan-601 /29) via the local FW, **tracked by IP SLA**
  to the DIST Vlan601 address; DIST tracks the s100 HSRP VIP back. DIST runs eBGP
  (AS 65001 → AS 65000, BFD) and announces the F5 VIP /24s.
- **FHRP:** HSRP v2 everywhere (now in Infrahub as `NetworkVRRPGroup`):
  s100 Vlan100 g1 per DC (RR-01 active), edge Vlan1 g1 and Et0/3 g11 (DC01-SW active),
  PE-VPN Gi1 g10 (DC01 active, **manual failover, no preempt by design**),
  LL3 v183 g183 per DC.

### 2.4 Partner services (as found)

| VRF | Partner (as described on device) | Attach | State today |
|---|---|---|---|
| v300 | "customer-z" (VRF) / "customer-y" (BGP desc), ASN 1170 | IKEv2 VTI Tunnel300 on PE-VPN, eBGP over tunnel | **Up** on DC01 (IKE READY, BGP 1 prefix: 100.64.100.1/32); DC02 standby Idle (expected) |
| v211 | "customer-y", ASN 1170 | eBGP on LL3 PEs to 172.31.91.6 / 172.32.91.6 | **Idle/never** on all 4 LL3 PEs — no v211 interface exists |
| v183 | "customer-h" | dot1q 183 sub-if on LL3 PEs + HSRP 183 | L2/L3 present, **no routing** (no BGP/static) |
| v146 | "customer-x" | IKEv1 keyring + isakmp profile on PE-VPN | **Dangling** — no tunnel/ipsec profile; VRF has no routes |

### 2.5 Live state worth knowing before writing intents

- **DC01 firewall path is DOWN.** DC01-RR-01/-02 `ip sla 1` → 10.245.128.9 times out,
  `track 1` Down, so DC01's s100 statics (10/8, 145.245.114/24, 145.245.115/24) are
  withdrawn; DC01-DIST `track 1` is Down too. DC01 PEs currently reach shared services
  and the F5 public ranges **only via DC02** (`10.0.0.0/8 via 10.245.246.253`).
  Nothing alerted. This is exactly the class of outage the resilience intent (BC-I3)
  must turn into a named, paging expectation.
- All OSPF adjacencies FULL, all LDP sessions Oper, all iBGP VPNv4 sessions Established.

---

## 3. Intent vocabulary for this network (supporting objects)

Proposed `IntentTenant`s (one per partner, plus the provider):

| Tenant | VRF | ASN | Notes |
|---|---|---|---|
| `provider-bootcamp` | s100, oob | 20 | owns core, shared services, management |
| `partner-v300` | v300 | 1170 | name to confirm (Q1) |
| `partner-v211` | v211 | 1170 | same ASN as v300 — same partner? (Q1) |
| `partner-v183` | v183 | ? | (Q1, Q5) |
| `partner-v146` | v146 | ? | retire or rebuild? (Q6) |

Proposed `IntentZone`s: `bootcamp-core` (core), `bootcamp-shared` (corp — s100 and
the F5 VIP ranges), `bootcamp-partner` (partner — every v<N>), `bootcamp-internet-edge`
(transit — 130.221.251.48/28, 100.120.0.0/24), `mgmt-oob` (existing, management).

`IntentCapability` per device type (drives the collector, ADR-0002 "collector is
derived, not chosen"):

| Device type | Capabilities | Consequence |
|---|---|---|
| C8000V | `ssh_cli`, `cli_config`, `netconf`(restconf enabled), `vpn_ipv4`, `mpls`, `ipsec` *(new)* | RESTCONF/YANG possible for PE-VPN |
| IOSv, IOSvL2, IOL L2 | `ssh_cli`, `cli_config`, `vpn_ipv4`/`mpls` (IOSv/IOSvL2) | **cli_scrape only** — no gNMI/NETCONF |

---

## 4. Intent statements

Each intent states the outcome (WHY), never a mechanism. Contracts carry the
measurable terms. IDs (`BC-…`) are stable and become the `derives_from` roots.

### BC-I1 `bootcamp-core-transport` — domain `routing`, priority **critical**
> Every PE and RR in both DCs can carry labelled VPN traffic to every other PE and RR,
> and every PE learns every VPN route from at least two reflectors.

- **Contract C1.1 `bootcamp-ibgp-rr` (RoutingContract, role `core`)**
  - peer_asn 20, afi_safis `vpnv4`, pe_devices = all PEs, RRs = all RRs.
  - Objective: each PE has 2 Established VPNv4 sessions to the RRs **of its own site**.
  - Objective: the 4 RRs are a full mesh (each RR: 3 RR sessions).
  - Objective: each RR reflects to every PE of its site (RR-client).
- **Contract C1.2 `bootcamp-underlay` (GenericContract)**
  - Objective `igp-adjacency`: every modelled core link has an OSPF adjacency in FULL.
  - Objective `label-path`: every core link has an LDP session Oper; every loopback
    in 10.245.245.0/24 ∪ 10.245.246.0/24 has a label.
  - Objective `loopback-reachability`: every loopback is in every router's RIB.
- **Invariants:** `bootcamp-rr-redundancy` (RedundancyInvariant, critical): ≥2
  RRs reachable per site; `bootcamp-dci-redundancy` (RedundancyInvariant, major):
  ≥2 DCI links up.
- **Policies:** `core-auth-required` (network): OSPF, LDP and iBGP sessions are
  authenticated; `rd-unique-per-pe` (network, see G-2).

### BC-I2 `bootcamp-shared-services` — domain `connectivity`, priority **critical**
> Every partner VRF and every PE reaches the shared services (10.0.0.0/8 and the F5
> public ranges 145.245.114.0/24, 145.245.115.0/24) through a firewall in a DC.

- **Contract C2.1 `s100-shared-vrf` (RoutingContract, role `core`)**
  - Tenant `provider-bootcamp`, VRF s100; export RT 20:100 from the gateways, import
    RT 20:199 (partner routes); PEs import 20:100.
  - allowed_prefixes (advertised into s100 by gateways): 10.0.0.0/8,
    145.245.114.0/24, 145.245.115.0/24, vlan-601 /29 of the site.
- **Contract C2.2 `s100-reachability` (ReachabilityContract)**
  - from_zone `bootcamp-partner` → to_prefixes above; `require_redundancy: true`;
    max_convergence_seconds: to define (Q8) — today the SLA track needs 60 s down/up
    + 3 s probe.
- **Invariants:** `s100-no-partner-to-partner` (NoLeakInvariant, critical): a
  partner VRF never imports another partner's routes (only 20:100 is imported);
  `s100-private-not-exported` (NoLeakInvariant, critical): RFC1918/bogon space
  learned from a partner is never tagged 20:199 except `private-s100`.

### BC-I3 `bootcamp-shared-services-resilience` — domain `resilience`, priority **high**
> Losing one firewall, one gateway router or one DC does not cut partners off the
> shared services, and a lost path is visible within one minute.

- **Contract C3.1 `s100-gateway-pair` (ReliabilityContract)**
  - min_pe_attachments 2 per site (RR-01/RR-02 HSRP pair on Vlan100),
    require_distinct_failure_domains `true` across sites.
  - Objective `fhrp-one-active`: exactly one active per s100 HSRP group, the other standby.
  - Objective `fw-path-tracked`: each site's gateway pair has a live firewall path
    (IP SLA OK) **or** the site's s100 routes are withdrawn (never blackholed).
- **Contract C3.2 `fw-dist-path` (GenericContract)** — DIST ↔ FW ↔ RR reachability per site.
- **Invariant:** `s100-two-sites-serving` (RedundancyInvariant, major): both sites
  originate the shared prefixes. **Currently violated** (DC01 path down, §2.5).

### BC-I4 `partner-ipsec-attachment` (pattern) — domain `connectivity`, priority per partner
> Partner *P* exchanges exactly its authorised prefixes with us over an encrypted
> path, and only reaches the public F5 VIP ranges.

This is the intent behind your v300 procedure; one **instance per partner**
(`partner-v300-connectivity`, …). The procedure is the IOS renderer for its
contracts.

- **Contract C4.1 `v<N>-ebgp` (RoutingContract, role `customer_edge`)**
  - peer_asn PARTNER_ASN (≠ 20), afi_safis `ipv4` in VRF v<N>,
    max_prefixes_ipv4 100, allowed_prefixes = PARTNER_PREFIXES (/32s),
    attach_communities RT 20:199, export set = `public-100`,
    pe_devices = the PE-VPN HSRP pair (DC01 + DC02 PE-VPN).
- **Contract C4.2 `v<N>-ipsec-tunnel` (GenericContract — new typed kind proposed, M-4)**
  - Terms: tunnel source = PE-VPN HSRP VIP, destination PARTNER_IP, tunnel /30 from
    the reserved range, IKEv2 profile set "ikev2-10/20", IPsec AES-256/SHA-256 PFS 16,
    lifetime 21600, DPD 10/2.
  - Objective: IKE SA READY and IPsec SA encaps/decaps increasing **on the active PE only**.
- **Contract C4.3 `v<N>-filtering` (SecurityContract, attach_interface_role `peering`)**
  - Rules: permit BGP to/from tunnel local IP; permit PARTNER_PREFIXES ↔ public /24s;
    implicit deny.
- **Policies:** `ebgp-max-prefix-required` (exists in lab, reuse); `partner-acl-required`;
  `pe-vpn-manual-failover` (operational: HSRP g10 is never preempting; failover is an
  operator action); `partner-side-ready-before-no-shut` (operational).
- **Invariants:** `partner-prefix-authorization` (PrefixAuthorizationInvariant,
  critical): accepted prefixes ⊆ allowed_prefixes and ∉ `private`;
  `partner-no-default` (NoDefaultOriginationInvariant, major) toward the partner.

### BC-I5 `partner-direct-attachment` (pattern) — domain `connectivity`
> Same promise as BC-I4 for partners attached by a VLAN on the LL3 PE pair (v211,
> v183), with a first-hop redundant gateway on our side.

Contracts as C4.1 + C4.3, plus `v<N>-attachment` (ReliabilityContract: two LL3 PEs,
HSRP group N). **Cannot be finalised** until Q5 is answered — today v211 has BGP but
no interface and v183 has an interface but no routing.

### BC-I6 `bootcamp-internet-edge` — domain `resilience`, priority **high**
> The partner-facing edge (130.221.251.48/28, 100.120.0.0/24) keeps a working gateway
> through the loss of either edge switch.

- Contract `edge-fhrp` (ReliabilityContract): HSRP g1 (Vlan1) and g11 (Et0/3), DC01-SW
  active / DC02-SW standby, preempt on; LAG Po1 between switches up with ≥1 member.
- Invariant `edge-l2-single-domain` (warning): documents that both DCs share one L2
  failure domain on this edge (see A-2).

### BC-I7 `bootcamp-management-plane` — domain `observability`, priority **high**
> Every bootcamp device is reachable and manageable out of band.

- Reuse the existing `OobReachabilityInvariant` (`fleet-oob-cabled`), scoped to both sites.
- Contract: every device has an `oob` VRF interface = Infrahub primary address, default
  route to 10.10.10.1, SSH reachable.
- Policy `mgmt-baseline` (network): NTP, remote syslog, AAA source — **none configured today** (G-12).

### BC-I8 `bootcamp-service-assurance` — domain `observability`
> Every promise above has fresh evidence; a dead collector reads as "unknown", never "fine".

One `IntentObservabilityContract` whose signals (§7) watch C1.x–C4.x. This is the
contract that compiles to collectors, intent-series and alert rules.

---

## 5. Derivation → configs (renderers)

Rule: the contract never names a mechanism; the renderer derives it. Derived facts
(never stored by hand):

| Derived value | Rule |
|---|---|
| RD of VRF v<N> on a PE | `<that PE's Loopback0>:<N>` — **per device** (see G-2/Q3); stored on `IpamVRFRouteDistinguisher` by the `vrf_rd` generator |
| iBGP neighbors of a PE | the RRs at the PE's site |
| RR mesh | all RRs, all sites |
| OSPF interfaces | every core link modelled between two core devices; others passive |
| LDP neighbors/passwords | the OSPF neighbors; password from vault |
| HSRP config | from `NetworkVRRPGroup` + `NetworkVRRPInterface` (priority), plus policy (preempt/timers — needs M-6) |
| Tunnel /30 | next free /30 from the reserved partner-tunnel pool (Infrahub `CoreIPPrefixPool`, Q4b) |
| Secrets | `{{vault:path#field}}` placeholders, resolved at push time (never in Infrahub) |

Renderer blocks per contract (IOS / IOS XE):

| Contract | Blocks emitted | Devices |
|---|---|---|
| C1.1 | `router bgp 20` neighbors + `address-family vpnv4` (+ RR-client / mesh peer-groups on RRs) | all PE, RR |
| C1.2 | Loopback0, `router ospf 1` (area 0 MD5, p2p, passive default), `mpls ldp` (neighbors, sync, advertise-labels ACL) | all PE, RR |
| C2.1 | `vrf definition s100` (RD derived, RT 20:100/20:199), `address-family ipv4 vrf s100` | RR, PE |
| C3.1/C3.2 | Vlan100 HSRP g1, `ip sla 1` + `track 1`, tracked `ip route vrf s100 …` | RR; DIST |
| C4.1 | `vrf definition v<N>` (export map, import 20:100), prefix-list `external-ip-v<N>`, `address-family ipv4 vrf v<N>` neighbor | PE-VPN pair |
| C4.2 | `crypto ikev2 profile v<N>`, `crypto ipsec profile v<N>`, `interface Tunnel<N>` | PE-VPN pair |
| C4.3 | `ip access-list extended v<N>-acl-ingress/egress`, `ip access-group` on Tunnel<N> | PE-VPN pair |
| BC-I6 | SW Vlan1/Et0/3 HSRP, Po1 LACP | SW pair |
| BC-I7 | `vrf definition oob`, oob interface, `ip route vrf oob 0/0 10.10.10.1`, (+ NTP/syslog/AAA once defined) | all |

The v300 procedure maps 1:1 to C4.1–C4.3 plus the pre-checks: its **VERIFICAÇÕES**
become merge-time invariant projections (RD unique, /30 free, PARTNER_IP unused,
partner prefixes ∉ `private`, ASN ≠ 20), its **ORDEM DE CRIAÇÃO** becomes plan stages,
its **VALIDAÇÃO** becomes the expectations below, and its "shared, do not change" list
is exactly the set of objects owned by C1.x/C2.1 (other intents) — a plan for BC-I4
must not touch them, and the planner can prove it by artifact diff.

## 6. Derivation → expectations

Ranges, never exact values; forbidden behaviour stated explicitly; freshness = 2×
collection interval. `observedVia` is derived from capabilities (all `cli_scrape`
today except PE-VPN, which could use RESTCONF).

| ID | Derives from | Predicate (holds when…) | Scope | Severity |
|---|---|---|---|---|
| E-1.1 | C1.1 | VPNv4 session state = Established, per (PE, local RR) | 4 sessions/site×PE | sev2 |
| E-1.2 | C1.1 | RR↔RR VPNv4 Established, per pair (6) | RRs | sev2 |
| E-1.3 | C1.2 | OSPF neighbor FULL per modelled core link | core links | sev2 |
| E-1.4 | C1.2 | LDP session Oper per OSPF neighbor | core links | sev2 |
| E-1.5 | C1.2 | every loopback present in RIB with label | PE, RR | sev2 |
| E-2.1 | C2.1 | 10.0.0.0/8, 145.245.114/24, 145.245.115/24 present in VRF s100 on every PE | PE | sev1 |
| E-2.2 | C2.1/inv | in every partner VRF, routes with RT ≠ 20:100 count = 0 (no partner→partner) | PE | sev1 |
| E-3.1 | C3.1 | per s100 HSRP group: count(active)=1 ∧ count(standby)=1 | RR pair/site | sev2 |
| E-3.2 | C3.1 | per site: IP SLA 1 = OK on both gateways **or** site's shared statics absent | RR | sev2 |
| E-3.3 | inv `s100-two-sites-serving` | both sites originate 10.0.0.0/8 into s100 (by RD origin) | RR | sev2 (**firing today**) |
| E-4.1 | C4.2 | on the HSRP-active PE-VPN: IKEv2 SA READY for PARTNER_IP; on the standby: no SA (expected) | PE-VPN pair | sev2 |
| E-4.2 | C4.2 | IPsec encaps and decaps counters increase within 2 intervals on the active PE | PE-VPN | sev3 |
| E-4.3 | C4.1 | eBGP v<N> Established on the active PE; Idle tolerated on the standby | PE-VPN | sev2 |
| E-4.4 | C4.1 | received prefixes ⊆ PARTNER_PREFIXES and 1 ≤ count ≤ max_prefixes | PE-VPN | sev1 |
| E-4.5 | C4.1 | advertised prefixes ⊆ {145.245.114.0/24, 145.245.115.0/24} | PE-VPN | sev1 |
| E-4.6 | C4.1 | on an RR: PARTNER_PREFIX present with RT 20:199 and RD `<PE-VPN lo>:<N>` | RR | sev3 |
| E-4.7 | C4.3 | ingress and egress ACL applied on Tunnel<N> (config fact) | PE-VPN pair | sev2 |
| E-4.8 | policy manual-failover | HSRP g10 preempt = off on both PE-VPNs (config fact) | PE-VPN pair | sev3 |
| E-6.1 | BC-I6 | edge HSRP g1/g11: one active, one standby; Po1 ≥1 bundled member each side | SW pair | sev2 |
| E-7.1 | BC-I7 | device answers SSH on its primary (oob) address | all | sev2 |
| E-8.x | BC-I8 | every signal above has a sample younger than 2× interval (`expectations-missing` / stale) | all | sev3 |

## 7. Derivation → signals (collectors, catalog)

The current `signal_catalog.py` knows `bgp_session_state`, `bgp_route_count`,
`policy_export_violation`, `interface_errors`, `device_reachability`. Proposed
additions (each needs a catalog entry + an IOS CLI-scrape parser; TOON/neterse
compressors exist for several of these commands):

| Signal id | Metric (raw fact) | Join keys | IOS/IOS XE source | Interval | Used by |
|---|---|---|---|---|---|
| `bgp_session_state` *(exists)* | `bgp_session_state_established` | device, neighbor, vrf | `show ip bgp vpnv4 all summary` | 30 s | E-1.1/1.2, E-4.3 |
| `bgp_route_count` *(exists)* | `bgp_session_prefixes_received/sent` | device, neighbor, vrf | same + `neighbors X routes/advertised-routes` | 60 s | E-4.4/4.5 |
| `ospf_adjacency` *(new)* | `ospf_neighbor_full` | device, neighbor, interface | `show ip ospf neighbor` | 30 s | E-1.3 |
| `ldp_session` *(new)* | `ldp_session_oper` | device, peer | `show mpls ldp neighbor` (IOSv: no `brief`) | 60 s | E-1.4 |
| `route_presence` *(new)* | `vrf_route_present{prefix}` | device, vrf, prefix | `show ip route vrf X <prefix>` / `show bgp vpnv4 vrf X` | 60 s | E-2.1, E-3.3, E-4.6 |
| `fhrp_state` *(new)* | `fhrp_group_state` (active/standby/other) | device, interface, group | `show standby brief` | 30 s | E-3.1, E-6.1, E-4.1 (active PE) |
| `ip_sla_state` *(new)* | `ip_sla_ok`, `track_up` | device, sla/track id | `show ip sla summary`, `show track brief` | 30 s | E-3.2 |
| `ike_sa_state` *(new)* | `ike_sa_ready` | device, peer | `show crypto ikev2 sa` | 30 s | E-4.1 |
| `ipsec_sa_counters` *(new)* | `ipsec_pkts_encaps/decaps` (counter) | device, peer | `show crypto ipsec sa peer X` | 60 s | E-4.2 |
| `config_fact` *(new)* | `config_fact{fact="acl_applied"|"hsrp_preempt"}` | device, object | running-config diff vs rendered artifact | 300 s | E-4.7, E-4.8 |
| `lag_members` *(new)* | `lag_bundled_members` | device, lag | `show etherchannel summary` | 60 s | E-6.1 |
| `device_reachability` *(exists)* | `device_up` | device | SSH probe on oob | 30 s | E-7.1 |

Join: per ADR-0002 the collectors emit raw facts keyed only by `device/neighbor/…`;
intent labels come from the `intent-series` artifact; alerts are joins; staleness is
per expected series.

---

## 8. Gaps found

### 8.1 Live defects / drift (the network disagrees with its own design)

| ID | Finding | Evidence | Intent it breaks |
|---|---|---|---|
| G-1 | **DC01 firewall path down; DC01 s100 statics withdrawn; no alert** | RR-01/02 `ip sla 1` Timeout, `track 1` Down; DIST `track 1` Down; DC01 PEs reach 10/8 via DC02 RR | BC-I3 (E-3.2, E-3.3) |
| G-2 | **RD collisions.** DC01-PE-LL3-02 uses LL3-01's loopback (10.245.245.251) for s100/v183/v211; DC02-PE-LL3-01 v183 = 10.245.245.251:183 (DC1); DC02-PE-LL3-02 v183 = 10.245.245.252:183 (DC1); DC02-PE-VPN v146/v300 = 10.245.245.246:N (DC1) | `rd` lines | C1/C4 — "RD = own Loopback0:N" rule; conflicts with v300 step 7 "same values" (Q3) |
| G-3 | **v211 cannot come up** on any LL3 PE: neighbor 172.31.91.6 / 172.32.91.6 has no connected interface in VRF v211; prefix-list `external-ip-v211` undefined; no `maximum-prefix` | BGP Idle "never" ×4 | BC-I5 |
| G-4 | LL3 PEs reference prefix-list `private` in `export-s100-shared` deny 20, but **`private` is not defined** there (only PE-VPN has it) — leak filter depends on undefined-list semantics | `ip prefix-list private` count 0 on all LL3 | inv `s100-private-not-exported` |
| G-5 | `public-100` differs: /23 exact on DC01-LL3-01 and DC02-LL3-02, two /24 elsewhere — with exact-match /23 the real /24s are not advertised | prefix-lists | C4.1 export set |
| G-6 | **DC02-PE-VPN deviates from the v300 standard:** Tunnel300 has **no ACLs**; ACL wildcards /25 not /24; no `maximum-prefix 100`; HSRP g10 **preempt on** (policy says manual failover); EEM applets (track 901) missing; RR neighbor descriptions say "RR-DC1" | config diff 114↔124 | C4.1/C4.3, policy manual-failover |
| G-7 | HSRP g183 on all LL3 PEs tracks object 1, **which does not exist** on those routers | `track` count 0 | BC-I5 |
| G-8 | v146 is a leftover: IKEv1 keyring (cleartext key) + isakmp profile, no tunnel/ipsec profile; VRF with `redistribute static` but no statics; 36 ISAKMP policies incl. SHA-1 and DH14 | PE-VPN config | BC-I4 (retire? Q6) |
| G-9 | VLAN 196 only trunked RR-01 → FW/LB in DC01; RR-02 and both DC02 RRs carry 197-198 only | `allowed vlan` | BC-I3 (gateway pair symmetry) |
| G-10 | DC02 RR Vlan99 OSPF is broadcast (DR/BDR, hello 1) vs p2p in DC01; DC02 RR SLA threshold 4 vs 2; DC02 RRs lack the 10.0.0.0/9 & 10.128.0.0/9 statics; DC02-PE-VPN lacks `mpls ldp sync` | twin diffs | C1.2, C3.1 consistency |
| G-11 | DC01-PE-LL3-01 OSPF has `network 10.245.140.0/22` (a DC2 range) | config | C1.2 |
| G-12 | No NTP, no remote syslog, no TACACS/RADIUS, no password encryption on any device; OSPF MD5 key is trivial and fleet-wide | config grep | BC-I7, `core-auth-required` |
| G-13 | Interface descriptions with copy-paste errors (e.g. DC01-RR-02 Gi1/1 describes itself as "DC01-RR-02-G1/1"; PE-VPN Gi1 says "DC01-SW-01-G0/1", but the switch is DC01-SW-LL2-01 and has no G0/1; DC02 PE-LL3/RR descriptions point at the wrong peer port) | descriptions | cosmetic, but they feed topology discovery |

### 8.2 Architecture gaps

| ID | Gap | Why it matters | Proposal |
|---|---|---|---|
| A-1 | PE-VPN tunnels are active/standby by HSRP with **manual** failover; a dead DC01 PE-VPN leaves partners down until a human acts | RTO is undefined | State it as a reliability term (RTO) + page on E-4.1 "no active tunnel anywhere"; decide if manual stays (Q8) |
| A-2 | The internet edge is one L2 domain stretched across DCs (SW Po1), HSRP across sites | a loop/storm or Po1 loss is a cross-DC event | record as warning invariant; consider per-DC L3 edges |
| A-3 | RRs are also the s100 data-plane gateways (HSRP, statics, SLA) | control-plane and services fate-share; RR maintenance = service failover | accept explicitly (waiver-style note) or split roles |
| A-4 | Shared services rely on one FW per DC with static+SLA, no dynamic routing | silent degradation (G-1 proves it) | expectation E-3.2/E-3.3 now; dynamic routing FW↔RR later |
| A-5 | No BFD in the core (only DIST eBGP); iBGP 8/24 timers | convergence budget for C2.2 is unknown | define max_convergence_seconds (Q8) |
| A-6 | **No telemetry transport except SSH:** UDP to 10.10.10.0/24 is dropped at 10.10.10.1 (SNMP, syslog, NetFlow impossible), no gNMI on IOSv/IOL | every signal is CLI scrape; collector load and freshness matter | collector = `scripts/cli_bgp_telemetry.py`-style scraper, extended per §7; RESTCONF for C8000V |
| A-7 | Unknown parties: partner endpoint 100.120.0.1, DIST eBGP peer 10.10.200.101 (AS 65000), DC02-DIST, LB-DC1 | intents can't reference what isn't modelled | onboard / describe (Q2) |

### 8.3 Model gaps (Infrahub schema / intent model)

| ID | Gap | Proposal |
|---|---|---|
| M-1 | `IpamVRF.vrf_rd` is one value per VRF; RDs are per PE | **Done (2026-11-22):** `IpamVRFRouteDistinguisher` (VRF × PE, `schemas/ipam_vrf.yml`) stores the RD, written by the `vrf_rd` generator as `<Loopback0>:<IpamVRF.vrf_id>` (`rd_mode: manual` to pin one); `vrf_rd`/`route_distinguisher` on IpamVRF deprecated. Migration: `scripts/migrate_vrf_instances.py` |
| M-10 | **Open:** no intent states "this VRF must be dual-homed" | contract `redundancy: dual_homed` / `min_instances`; invariant: ≥2 instances serve the same CE, each with a distinct RD (+ `maximum-paths ibgp` rendered) |
| M-11 | RD uniqueness can't be a schema constraint (`unique: true` rejects a second empty RD); the members peer can't exclude switchports | **Done (2026-11-22):** global check `vrf_instance_integrity_check` (`checks/vrf_instance_integrity.py`): RD unique network-wide, manual ⇒ RD set, members routed (no `l2_mode`), on the instance's device, in one VRF only. Members are `InterfaceLayer3`; a VLAN reaches a VRF via its SVI |
| M-12 | **Open:** instances are data; contracts' `pe_devices` imply them too (two sources of "which PE carries the VRF") | generator creates instances for contract PEs, or a check flags a contract PE without an instance |
| M-2 | Route targets not populated; the 20:199 tagging is a route-map side effect | populate `IpamRouteTarget` 20:100/20:199; express tagging as `attach_communities` on C4.1 |
| M-3 | `IpamIPAddress.address` is globally unique → the shared tunnel IP (192.168.28.193 on both PE-VPNs, standby copy **by design**) can't be modelled; today DC02 Tunnel300 has no IP in Infrahub | model tunnel IP once, bound to the HSRP group / contract, rendered to both PEs |
| M-4 | No IPsec/IKE/tunnel kinds; partner endpoint not modelled | new typed contract `IntentIPsecAttachmentContract` (or GenericContract + objectives first) + an external-peer object |
| M-5 | `NetworkVRRPGroup` lacks protocol/preempt/timers/track (loaded as-is today) | needed to render HSRP and check `pe-vpn-manual-failover` from the SoT (extend later) |
| M-6 | No static-route-with-tracking kind | needed for C3.1 rendering (RR s100 statics, DIST statics) |
| M-7 | Signal catalog lacks the 9 signals in §7 | add catalog entries + parsers; reuse neterse compressors |
| M-8 | IOSv / IOSvL2 / IOL L2 carry no capabilities (C8000V copied from `cisco_iosxe` 2026-11-22; the relationship moved to `DcimDeviceType`) | assign per §3 so `observedVia` derives |
| M-9 | Existing intents/zones/tenants are the clab customer-c lab | new tenants/zones in §3; keep the two worlds separate by `IntentZone` |

---

## 9. What I need from you

| # | Question | Blocks |
|---|---|---|
| Q1 | Partner names/tenants: v300 is "customer-z" on the VRF but "customer-y" on BGP and shares ASN 1170 with v211 ("customer-y"). One partner with two services, or two partners? Names for v146 (customer-x) and v183 (customer-h)? | §3 tenants, BC-I4/I5 |
| Q2 | What are 100.120.0.1 (partner endpoint), 10.10.200.101 / AS 65000 (DIST eBGP peer)? Should DC02-DIST and LB-DC1 be onboarded? | A-7, BC-I3 |
| Q3 | RD rule: unique per PE (`own loopback:N`, recommended) or identical on both PEs of a pair (as v300 step 7 implies and DC02 does today)? | G-2, renderer |
| Q4 | (a) Canonical `public-100`: two /24 (recommended, matches PE-VPN) or the /23? (b) The reserved range for partner tunnel /30s ("Faixa reservada: <informar>") | G-5, C4.2 pool |
| Q5 | Intended design for v211 and v183 on the LL3 PEs (attachment VLAN/subnet, BGP or static, who is the CE)? | BC-I5, G-3, G-7 |
| Q6 | v146: retire the IKEv1 remnants, or rebuild on the v300 (IKEv2) pattern? | G-8 |
| Q7 | Monitoring target: which Prometheus/Grafana (this repo's `monitoring/` stack or another)? Is the UDP block on 10.10.10.0/24 permanent (forces CLI scrape)? | §7, A-6 |
| Q8 | Priorities/owners per intent, change windows for the bootcamp, the convergence budget for s100 failover, and whether PE-VPN failover stays manual | §4, A-1, A-5 |
| Q9 | Should DC01's firewall path (G-1) be fixed first, or written as the first intent violation the new expectations must catch? | G-1 |

## 10. Next steps (once §9 is answered)

1. Load tenants/zones/capabilities and BC-I1…I8 (+ one BC-I4 instance for v300) on an
   Infrahub **branch**; nothing goes to `main` until reviewed.
2. Add the §7 signals to `signal_catalog.py` + parsers; extend the CLI-scrape collector.
3. Write the IOS renderers for C1–C4 (start with C4: the v300 procedure is already the spec),
   render artifacts for all 13 devices and **diff against today's running-config** —
   the diff is the brownfield gap list (should reproduce §8.1).
4. Compile expectations + intent-series + alert rules; dry-run them against the live
   network (E-3.3 must fire today).
5. Plan remediation of §8.1 as separate changes, each gated by its expectations.
