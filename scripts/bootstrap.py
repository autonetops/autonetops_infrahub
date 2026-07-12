#!/usr/bin/env python3
"""Seed the intent lab into InfraHub.

Loads the full customer-c scenario: taxonomy (zones, capabilities),
inventory (devices, interfaces, cabling - including the OOB management
plane), IPAM, and the intent hierarchy

    realm -> intent -> policy -> contracts + invariants

across the routing, reachability, security, observability and reliability
realms (compliance and performance are seeded as empty realms), plus the
groups that drive targeted checks and artifact definitions.

Prerequisites (run in this order):
    infrahubctl schema load schema-library/base
    infrahubctl schema load schema-library/extensions/vrf \
        schema-library/extensions/cable \
        schema-library/extensions/location_minimal \
        schema-library/extensions/routing_bgp
    infrahubctl schema load schemas/intent.yml
    python scripts/bootstrap.py

Environment: INFRAHUB_ADDRESS (default http://localhost:8000) and
INFRAHUB_API_TOKEN.

Idempotency: nodes with unique names upsert cleanly; cables have no
human-friendly ID, so re-running on the same branch may duplicate them.
Prefer running once per fresh branch.
"""

import os
import sys

from infrahub_sdk import Config, InfrahubClientSync

BRANCH = os.environ.get("INFRAHUB_BRANCH", "main")

MGMT_NET = "172.20.20"
OOB_NET = "192.168.100"


def upsert(client, kind, **data):
    obj = client.create(kind=kind, branch=BRANCH, **data)
    obj.save(allow_upsert=True)
    return obj


def main():
    client = InfrahubClientSync(
        address=os.environ.get("INFRAHUB_ADDRESS", "http://localhost:8000"),
        config=Config(api_token=os.environ.get("INFRAHUB_API_TOKEN")),
    )

    # ------------------------------------------------------------------
    # Organizations, ASNs, locations
    # ------------------------------------------------------------------
    print("==> organizations / ASNs / locations")
    provider = upsert(client, "OrganizationProvider", name="AutoNetOps")
    cust_c_org = upsert(client, "OrganizationProvider", name="Customer C")
    inet_org = upsert(client, "OrganizationProvider", name="InetPeerCo")

    manufacturers = {
        name: upsert(client, "OrganizationManufacturer", name=name)
        for name in ("Cisco Systems", "Arista Networks", "Juniper Networks",
                     "Nokia", "FRRouting")
    }

    as65010 = upsert(client, "RoutingAutonomousSystem", name="AutoNetOps",
                     asn=65010, organization=provider.id)
    as65123 = upsert(client, "RoutingAutonomousSystem", name="Customer C",
                     asn=65123, organization=cust_c_org.id)
    as64999 = upsert(client, "RoutingAutonomousSystem", name="InetPeerCo",
                     asn=64999, organization=inet_org.id)

    pop_a = upsert(client, "LocationSite", name="EMEA POP A", shortname="emea-pop-a")
    pop_b = upsert(client, "LocationSite", name="EMEA POP B", shortname="emea-pop-b")

    # ------------------------------------------------------------------
    # Taxonomy: capabilities and zones (controlled vocabularies)
    # ------------------------------------------------------------------
    print("==> capability & zone taxonomy")
    capabilities = {}
    for name, category, description in [
        ("gnmi", "telemetry", "Streaming telemetry + config via gNMI"),
        ("snmp", "telemetry", "Legacy polling"),
        ("ssh_cli", "telemetry", "CLI scraping over SSH - the fallback evidence path"),
        ("bmp_client", "telemetry", "BGP Monitoring Protocol export"),
        ("gnmi_set", "management", "Config push via gNMI Set"),
        ("netconf", "management", "Config push via NETCONF"),
        ("cli_config", "management", "Config push via CLI/SSH"),
        ("mpls", "forwarding", "MPLS forwarding (LDP)"),
        ("vpn_ipv4", "forwarding", "BGP L3VPN address family"),
    ]:
        capabilities[name] = upsert(
            client, "IntentCapability",
            name=name, category=category, description=description,
        )

    zones = {}
    for name, zone_type in [
        ("corp-emea", "corp"),
        ("core", "core"),
        ("internet-peers", "internet_peering"),
        ("transit", "transit"),
        ("mgmt-oob", "management"),
    ]:
        zones[name] = upsert(client, "IntentZone", name=name, zone_type=zone_type)

    # ------------------------------------------------------------------
    # Platforms - capabilities attached HERE drive renderer + collector
    # selection. The Cisco IOL image has no gNMI: it claims ssh_cli and
    # netconf, so the observability compiler would emit a CLI-scrape
    # collector for it rather than a gNMI subscription.
    #
    # juniper_junos keeps a platform record even though no device runs it
    # today - the Junos renderer stays registered so a node can move back
    # onto vJunos as a one-field change here and nothing else.
    #
    # nokia_srlinux no longer claims `mpls`/`vpn_ipv4`: the free 7220 IXR
    # image the lab runs has no MPLS subsystem at all (a Nokia chassis
    # license unlocks the 7250 platforms that do). SR Linux is a customer
    # edge here, which needs neither - it speaks plain eBGP ipv4-unicast.
    # cisco_iosxe keeps both: core-rr-01 (Cisco IOL) is the vpn-ipv4 route
    # reflector, and IOL's image is full MPLS L3VPN.
    # ------------------------------------------------------------------
    print("==> platforms")
    platforms = {}
    platform_defs = [
        ("cisco_iosxe", "Cisco Systems", "cisco_ios", "cisco_iol",
         ["ssh_cli", "snmp", "netconf", "cli_config", "mpls", "vpn_ipv4"]),
        ("arista_eos", "Arista Networks", "arista_eos", "ceos",
         ["gnmi", "gnmi_set", "netconf", "ssh_cli", "snmp", "cli_config",
          "mpls", "vpn_ipv4"]),
        ("nokia_srlinux", "Nokia", "nokia_srl", "nokia_srlinux",
         ["gnmi", "gnmi_set", "netconf", "ssh_cli", "snmp", "cli_config"]),
        ("juniper_junos", "Juniper Networks", "juniper_junos",
         "juniper_vjunosrouter",
         ["gnmi", "netconf", "ssh_cli", "snmp", "cli_config", "mpls",
          "vpn_ipv4"]),
        ("frr", "FRRouting", "linux", "linux", ["cli_config"]),
        ("linux_bridge", "FRRouting", "linux", "linux", []),
    ]
    for name, mfr, netmiko, clab_os, caps in platform_defs:
        platforms[name] = upsert(
            client, "DcimPlatform",
            name=name,
            manufacturer=manufacturers[mfr].id,
            netmiko_device_type=netmiko,
            containerlab_os=clab_os,
            capabilities=[capabilities[c].id for c in caps],
        )

    # ------------------------------------------------------------------
    # IPAM: tenant VRF, route targets, prefixes
    # ------------------------------------------------------------------
    print("==> IPAM")
    default_ns = client.get(kind="BuiltinIPNamespace", name__value="default",
                            branch=BRANCH)
    rt = upsert(client, "IpamRouteTarget", name="target:65010:1203")
    vrf = upsert(client, "IpamVRF", name="CUSTC-PROD", vrf_rd="65010:1203",
                 namespace=default_ns.id, import_rt=rt.id, export_rt=rt.id)

    prefixes = {}
    for cidr, in_vrf in [
        ("10.84.0.0/16", True),
        ("10.84.20.0/24", True),
        ("2001:db8:84::/48", True),
        ("198.51.100.0/24", False),
    ]:
        data = {"prefix": cidr, "status": "active"}
        if in_vrf:
            data["vrf"] = vrf.id
        prefixes[cidr] = upsert(client, "IpamPrefix", **data)

    # ------------------------------------------------------------------
    # Devices, interfaces, addressing, cabling (incl. the OOB plane)
    # ------------------------------------------------------------------
    print("==> devices & interfaces")

    # name: (platform, role, asn, site, mgmt_ip, interfaces)
    # interface tuple: (name, kind, role, ip, description)
    device_defs = {
        "pe-emea-01": ("arista_eos", "edge", as65010, pop_a, f"{MGMT_NET}.11", [
            ("Loopback0", "virtual", None, "10.255.0.1/32", "router-id"),
            ("Ethernet1", "physical", "core", "10.100.0.0/31", "to core-rr-01"),
            ("Ethernet2", "physical", "cust", "10.84.255.1/30", "to ce-custc-01 [customer-c]"),
            ("Ethernet3", "physical", "management", f"{OOB_NET}.11/24", "OOB"),
        ]),
        "pe-emea-02": ("arista_eos", "edge", as65010, pop_b, f"{MGMT_NET}.12", [
            ("Loopback0", "virtual", None, "10.255.0.2/32", "router-id"),
            ("Ethernet1", "physical", "core", "10.100.0.2/31", "to core-rr-01"),
            ("Ethernet2", "physical", "cust", "10.84.255.5/30", "to ce-custc-02 [customer-c]"),
            ("Ethernet3", "physical", "peering", "203.0.113.0/31", "to peer-inet-01"),
            ("Ethernet4", "physical", "management", f"{OOB_NET}.12/24", "OOB"),
        ]),
        # core-rr-01 is the vpn-ipv4 route reflector. It ran on SR Linux
        # until the lab had to run on a host with no Nokia chassis license:
        # the free 7220 IXR image has no MPLS, so a vpn-ipv4 reflector is
        # impossible there. It moved to Cisco IOL (full MPLS L3VPN, no
        # license) using IOS `Loopback0`/`Ethernet0/N` names; the SR Linux
        # free tier moved to the customer edges below. The intent did not
        # move - the Junos renderer is also still registered, so a reflector
        # can return to vJunos or a licensed 7250 as a one-field change.
        "core-rr-01": ("cisco_iosxe", "core", as65010, pop_a, f"{MGMT_NET}.13", [
            ("Loopback0", "virtual", None, "10.255.0.3/32", "router-id"),
            ("Ethernet0/1", "physical", "core", "10.100.0.1/31", "to pe-emea-01"),
            ("Ethernet0/2", "physical", "core", "10.100.0.3/31", "to pe-emea-02"),
            ("Ethernet0/3", "physical", "management", f"{OOB_NET}.13/24", "OOB"),
        ]),
        # The customer edges run SR Linux on the free 7220 IXR-D3L: a CE
        # needs only eBGP ipv4-unicast, which the unlicensed chassis does.
        # No loopback is modeled - the customer-facing address is the BGP
        # router-id (see render_nokia_srlinux). Ports are `ethernet-1/N`.
        "ce-custc-01": ("nokia_srlinux", "cpe", as65123, pop_a, f"{MGMT_NET}.21", [
            ("ethernet-1/1", "physical", "cust", "10.84.255.2/30", "to pe-emea-01"),
            ("ethernet-1/2", "physical", "management", f"{OOB_NET}.21/24", "OOB"),
        ]),
        "ce-custc-02": ("nokia_srlinux", "cpe", as65123, pop_b, f"{MGMT_NET}.22", [
            ("ethernet-1/1", "physical", "cust", "10.84.255.6/30", "to pe-emea-02"),
            ("ethernet-1/2", "physical", "management", f"{OOB_NET}.22/24", "OOB"),
        ]),
        "peer-inet-01": ("frr", "edge", as64999, pop_b, f"{MGMT_NET}.31", [
            ("eth1", "physical", "peering", "203.0.113.1/31", "to pe-emea-02"),
            ("eth2", "physical", "management", f"{OOB_NET}.31/24", "OOB"),
        ]),
        "oob-sw-01": ("linux_bridge", "tor", None, pop_a, f"{MGMT_NET}.41", [
            (f"eth{i}", "physical", "management", None, f"OOB port {i}")
            for i in range(1, 7)
        ]),
    }

    devices, interfaces = {}, {}
    for name, (platform, role, asn, site, mgmt_ip, iface_defs) in device_defs.items():
        mgmt_addr = upsert(client, "IpamIPAddress", address=f"{mgmt_ip}/24")
        devices[name] = upsert(
            client, "DcimDevice",
            name=name, status="active", role=role,
            platform=platforms[platform].id,
            location=site.id,
            primary_address=mgmt_addr.id,
            **({"asn": asn.id} if asn else {}),
        )
        for iface_name, kind, iface_role, ip, description in iface_defs:
            data = {
                "name": iface_name,
                "device": devices[name].id,
                "status": "active",
                "description": description,
            }
            if iface_role:
                data["role"] = iface_role
            if ip:
                addr = upsert(client, "IpamIPAddress", address=ip)
                data["ip_addresses"] = [addr.id]
            iface_kind = "InterfacePhysical" if kind == "physical" else "InterfaceVirtual"
            interfaces[(name, iface_name)] = upsert(client, iface_kind, **data)

    print("==> cabling (core, customer, peering and OOB planes)")
    cables = [
        (("pe-emea-01", "Ethernet1"), ("core-rr-01", "Ethernet0/1")),
        (("pe-emea-02", "Ethernet1"), ("core-rr-01", "Ethernet0/2")),
        (("ce-custc-01", "ethernet-1/1"), ("pe-emea-01", "Ethernet2")),
        (("ce-custc-02", "ethernet-1/1"), ("pe-emea-02", "Ethernet2")),
        (("peer-inet-01", "eth1"), ("pe-emea-02", "Ethernet3")),
        # the OOB plane - this is what makes the oob_reachability check pass
        (("pe-emea-01", "Ethernet3"), ("oob-sw-01", "eth1")),
        (("pe-emea-02", "Ethernet4"), ("oob-sw-01", "eth2")),
        (("core-rr-01", "Ethernet0/3"), ("oob-sw-01", "eth3")),
        (("ce-custc-01", "ethernet-1/2"), ("oob-sw-01", "eth4")),
        (("ce-custc-02", "ethernet-1/2"), ("oob-sw-01", "eth5")),
        (("peer-inet-01", "eth2"), ("oob-sw-01", "eth6")),
    ]
    for end_a, end_b in cables:
        try:
            upsert(
                client, "DcimCable",
                status="connected",
                connected_endpoints=[interfaces[end_a].id, interfaces[end_b].id],
            )
        except Exception as exc:
            # cables have no unique key, so a re-run tries to re-cable ports
            # that already have a peer - that is the only error we tolerate
            if "maximum of 1 allowed" not in str(exc):
                raise
            print(f"    cable {end_a} <-> {end_b} already present, skipping")

    # ------------------------------------------------------------------
    # Intent layer: realm -> intent -> policy -> contracts + invariants
    # ------------------------------------------------------------------
    print("==> realms")
    realm_defs = [
        ("routing", "How reachability information is exchanged: BGP relationships, announcements, path preference."),
        ("reachability", "Who must reach what, and how fast the network must converge back to it."),
        ("security", "Segmentation, filtering and hardening posture."),
        ("observability", "What must be measured, how fresh the evidence must be, what pages."),
        ("reliability", "Redundancy and failure-domain requirements."),
        ("compliance", "Golden-config, software lifecycle and audit conformance."),
        ("performance", "QoS, latency budgets and capacity headroom."),
    ]
    realms = {
        name: upsert(client, "IntentRealm", name=name, description=desc)
        for name, desc in realm_defs
    }

    print("==> tenant")
    tenant = upsert(
        client, "IntentTenant",
        name="customer-c",
        description="Enterprise customer C - MPLS L3VPN + Internet breakout",
        environment="prod",
        owner="customer-success-emea",
        ticket="CHG-2025-1209",
        tenant_community="65010:1203",
        vrfs=[vrf.id],
    )

    def intent(name, realm, statement, **data):
        return upsert(client, "IntentIntent", name=name,
                      realm=realms[realm].id, statement=statement, **data)

    def policy(name, parent, description, enforcement="full"):
        return upsert(client, "IntentPolicy", name=name, intent=parent.id,
                      description=description, enforcement=enforcement)

    def invariant(name, parent, invariant_type, description, severity="blocking"):
        return upsert(client, "IntentInvariant", name=name, policy=parent.id,
                      invariant_type=invariant_type, severity=severity,
                      description=description)

    # ---------------------------------------------------- realm: routing
    print("==> routing realm")
    i_custc_vpn = intent(
        "custc-l3vpn-connectivity", "routing",
        "Customer C sites exchange routes privately over our L3VPN; only "
        "authorized prefixes are announced and they never reach a public peer.",
        tenant=tenant.id, priority="high", owner="customer-success-emea",
        ticket="CHG-2025-1209",
    )
    p_custc_edge = policy(
        "custc-edge-routing", i_custc_vpn,
        "eBGP handoff from customer C CEs into CUSTC-PROD on redundant PEs.")
    contract_ce = upsert(
        client, "IntentRoutingContract",
        name="custc-ce-to-pe",
        description="Customer C CE handoff into CUSTC-PROD",
        policy=p_custc_edge.id,
        role="customer_edge",
        peer_asn=65123,
        afi_safis=["ipv4-unicast", "ipv6-unicast"],
        max_prefixes_ipv4=200,
        max_prefixes_ipv6=100,
        local_preference=120,
        require_communities=["65010:1203", "65010:30010"],
        attach_communities=["65010:55555"],
        zone=zones["corp-emea"].id,
        pe_devices=[devices["pe-emea-01"].id, devices["pe-emea-02"].id],
        ce_devices=[devices["ce-custc-01"].id, devices["ce-custc-02"].id],
        allowed_prefixes=[
            prefixes["10.84.0.0/16"].id,
            prefixes["10.84.20.0/24"].id,
            prefixes["2001:db8:84::/48"].id,
        ],
        export_deny_zones=[zones["internet-peers"].id, zones["transit"].id],
    )
    invariant("custc-no-leak", p_custc_edge, "no_leak",
              "Customer C routes must not be exported to any public peer or transit")
    invariant("custc-no-default", p_custc_edge, "no_default_origination",
              "Do not originate default into the CE unless explicitly enabled")
    invariant("custc-prefix-authorization", p_custc_edge, "prefix_authorization",
              "Only prefixes owned by customer C's VRFs may be allowed on the contract")

    i_core = intent(
        "provider-core-transport", "routing",
        "Every PE learns every VPN route through reflected iBGP - no full mesh "
        "to maintain, one reflector to reason about.",
        priority="critical", owner="backbone-engineering",
    )
    p_core = policy(
        "core-route-reflection", i_core,
        "vpn-ipv4 route reflection: PEs are clients of core-rr-01.")
    contract_core = upsert(
        client, "IntentRoutingContract",
        name="ibgp-core-rr",
        description="vpn-ipv4 route reflection - PEs are clients of core-rr-01",
        policy=p_core.id,
        role="core",
        peer_asn=65010,
        afi_safis=["vpn-ipv4"],
        zone=zones["core"].id,
        pe_devices=[devices["core-rr-01"].id],
        ce_devices=[devices["pe-emea-01"].id, devices["pe-emea-02"].id],
    )

    i_peering = intent(
        "provider-internet-peering", "routing",
        "AS65010 exchanges internet routes with InetPeerCo in EMEA; nothing "
        "tenant-owned ever appears at the exchange.",
        priority="high", owner="peering-team",
    )
    p_peering = policy(
        "emea-peering", i_peering,
        "Public eBGP session with InetPeerCo on pe-emea-02.")
    contract_inet = upsert(
        client, "IntentRoutingContract",
        name="inet-peering-emea",
        description="Public peering with InetPeerCo - customer-c must NEVER appear here",
        policy=p_peering.id,
        role="internet_peering",
        peer_asn=64999,
        afi_safis=["ipv4-unicast"],
        max_prefixes_ipv4=50,
        zone=zones["internet-peers"].id,
        pe_devices=[devices["pe-emea-02"].id],
        ce_devices=[devices["peer-inet-01"].id],
        allowed_prefixes=[prefixes["198.51.100.0/24"].id],
        export_tenants=[],  # empty on purpose: nothing tenant-owned goes out
    )
    invariant("inet-no-reorigination", p_peering, "no_reorigination",
              "Peer routes keep their original AS path - we never re-originate")

    # ------------------------------------------------ realm: reachability
    print("==> reachability realm")
    i_reach = intent(
        "custc-apps-reachable", "reachability",
        "Customer C application prefixes stay reachable from EMEA corp and "
        "recover within 30 seconds of any single failure.",
        tenant=tenant.id, priority="high", owner="customer-success-emea",
    )
    p_reach = policy(
        "custc-reachability", i_reach,
        "Corp-EMEA reaches the customer C application ranges via the L3VPN.")
    upsert(
        client, "IntentReachabilityContract",
        name="custc-apps-reachable-from-emea-corp",
        policy=p_reach.id,
        from_zone=zones["corp-emea"].id,
        to_prefixes=[prefixes["10.84.0.0/16"].id, prefixes["2001:db8:84::/48"].id],
        require_redundancy=True,
        max_convergence_seconds=30,
    )
    invariant("custc-reachability-preserved", p_reach, "reachability_preserved",
              "A change may not remove the last redundant path to these prefixes",
              severity="warning")

    # ---------------------------------------------------- realm: security
    print("==> security realm")
    i_sec = intent(
        "custc-perimeter-protection", "security",
        "Only sanctioned traffic enters customer C's VPN at the provider edge.",
        tenant=tenant.id, priority="high", owner="secops-emea",
    )
    p_sec = policy(
        "custc-inbound-protect", i_sec,
        "Inbound filtering and DDoS posture on every customer-facing PE port.")
    contract_sec = upsert(
        client, "IntentSecurityContract",
        name="custc-inbound-protect",
        policy=p_sec.id,
        ddos_profile="standard_l3vpn",
        attach_device_role="pe",
        attach_interface_role="cust",
    )
    upsert(client, "IntentSecurityRule", contract=contract_sec.id,
           name="allow-corp-https", index=10, action="allow", protocol="tcp",
           dst_ports=[443, 8443], src_zone=zones["corp-emea"].id,
           dst_prefixes=[prefixes["10.84.20.0/24"].id])
    upsert(client, "IntentSecurityRule", contract=contract_sec.id,
           name="deny-internet-to-tenant", index=20, action="deny", protocol="any",
           src_zone=zones["internet-peers"].id,
           dst_prefixes=[prefixes["10.84.0.0/16"].id])

    # ------------------------------------------------- realm: reliability
    print("==> reliability realm")
    i_rel = intent(
        "custc-resilience", "reliability",
        "Customer C survives the loss of any single PE or POP without losing "
        "the VPN service.",
        tenant=tenant.id, priority="high", owner="customer-success-emea",
    )
    p_rel = policy(
        "custc-redundancy", i_rel,
        "Dual-homed CEs across distinct failure domains.",
        enforcement="design_time")
    upsert(
        client, "IntentReliabilityContract",
        name="custc-reliability",
        policy=p_rel.id,
        min_pe_attachments=2,
        require_distinct_failure_domains=True,
    )
    invariant("custc-redundancy-floor", p_rel, "redundancy",
              "At least two PE attachments in distinct failure domains at all times")

    # ----------------------------------------------- realm: observability
    print("==> observability realm")
    i_assure = intent(
        "custc-service-assurance", "observability",
        "Customer C's service health is continuously evidenced: session state, "
        "route counts and export violations page before the customer notices.",
        tenant=tenant.id, priority="high", owner="noc-emea",
    )
    p_assure = policy(
        "custc-telemetry", i_assure,
        "Collect and alert on the signals that prove the routing contracts hold.",
        enforcement="runtime")
    contract_obs_custc = upsert(
        client, "IntentObservabilityContract",
        name="custc-service-signals",
        description="Signals that keep customer C's routing contracts honest",
        policy=p_assure.id,
    )
    upsert(client, "IntentObservabilitySignal",
           name="custc-bgp-session-state", signal="bgp_session_state",
           frequency_seconds=10, severity="sev2", condition="down_for > 60s",
           contract=contract_obs_custc.id, watches=contract_ce.id)
    upsert(client, "IntentObservabilitySignal",
           name="custc-bgp-route-count", signal="bgp_route_count",
           direction="inbound", frequency_seconds=30, severity="sev2",
           condition="count > max_prefixes",
           contract=contract_obs_custc.id, watches=contract_ce.id)
    upsert(client, "IntentObservabilitySignal",
           name="inet-peer-export-violation", signal="policy_export_violation",
           direction="outbound", frequency_seconds=30, severity="sev1",
           condition="count > 5",
           contract=contract_obs_custc.id, watches=contract_inet.id)
    invariant("custc-observable", p_assure, "capability_present",
              "Every device a watched contract runs on must expose a telemetry capability")

    i_fleet = intent(
        "fleet-management-plane", "observability",
        "Every production device stays reachable over the out-of-band "
        "management plane, independent of the forwarding plane.",
        priority="critical", owner="noc-emea",
    )
    p_fleet = policy(
        "fleet-oob-monitoring", i_fleet,
        "Ping every device over OOB; alert on loss.",
        enforcement="runtime")
    contract_obs_fleet = upsert(
        client, "IntentObservabilityContract",
        name="fleet-baseline",
        description="Fleet-wide management-plane signals",
        policy=p_fleet.id,
    )
    upsert(client, "IntentObservabilitySignal",
           name="fleet-oob-reachability", signal="device_reachability",
           frequency_seconds=60, severity="sev3", condition="loss > 50%",
           contract=contract_obs_fleet.id)
    invariant("fleet-oob-cabled", p_fleet, "oob_reachability",
              "Every device keeps a management interface cabled to the OOB plane")

    # ------------------------------------------------------------------
    # Change windows: WHEN plans may dispatch (read by the orchestrator)
    # ------------------------------------------------------------------
    print("==> change windows")
    routers_named = ["pe-emea-01", "pe-emea-02", "core-rr-01"]
    upsert(client, "OpsChangeWindow",
           name="lab-continuous",
           description="Lab-only always-open window; delete it to watch the "
                       "orchestrator defer to the real maintenance windows.",
           window_type="maintenance",
           days=["monday", "tuesday", "wednesday", "thursday", "friday",
                 "saturday", "sunday"],
           start_utc="00:00", duration_minutes=1440,
           max_blast_radius="moderate")
    upsert(client, "OpsChangeWindow",
           name="emea-standard-maintenance",
           description="Weekly EMEA core/edge maintenance",
           window_type="maintenance",
           days=["tuesday", "thursday"],
           start_utc="21:00", duration_minutes=240,
           max_blast_radius="high",
           devices=[devices[n].id for n in routers_named])
    upsert(client, "OpsChangeWindow",
           name="weekday-low-risk",
           description="Low-blast-radius changes may ride business hours",
           window_type="maintenance",
           days=["monday", "tuesday", "wednesday", "thursday", "friday"],
           start_utc="08:00", duration_minutes=600,
           max_blast_radius="low")
    upsert(client, "OpsChangeWindow",
           name="q3-core-audit-freeze",
           description="Core untouchable during the Q3 audit capture",
           window_type="freeze",
           starts_at="2026-07-20T00:00:00Z", ends_at="2026-07-27T00:00:00Z",
           devices=[devices["core-rr-01"].id])

    # ------------------------------------------------------------------
    # Groups: targeted checks + artifact targets
    # ------------------------------------------------------------------
    print("==> groups")
    routers = [d.id for n, d in devices.items() if n != "oob-sw-01"]
    upsert(client, "CoreStandardGroup", name="network_devices",
           members=list(devices[d].id for d in devices))
    upsert(client, "CoreStandardGroup", name="monitored_devices", members=routers)
    upsert(client, "CoreStandardGroup", name="oob_switches",
           members=[devices["oob-sw-01"].id])
    upsert(client, "CoreStandardGroup", name="routing_contracts",
           members=[contract_ce.id, contract_inet.id, contract_core.id])
    upsert(client, "CoreStandardGroup", name="observability_contracts",
           members=[contract_obs_custc.id, contract_obs_fleet.id])
    upsert(client, "CoreStandardGroup", name="intent_tenants", members=[tenant.id])

    # legacy group from the flat model - drop it if it survived a migration
    try:
        legacy = client.get(kind="CoreStandardGroup",
                            name__value="intent_contracts", branch=BRANCH)
        legacy.delete()
        print("    dropped legacy group intent_contracts")
    except Exception:
        pass

    print("done. Open a Proposed Change and watch the invariants run.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # surface SDK errors with context
        print(f"bootstrap failed: {exc}", file=sys.stderr)
        raise
