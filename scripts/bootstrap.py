#!/usr/bin/env python3
"""Seed the intent lab into InfraHub.

Loads the full customer-c scenario from the article "Modeling Intent for
Real Networks": taxonomy (zones, capabilities), inventory (devices,
interfaces, cabling - including the OOB management plane), IPAM, and the
intent layer (tenant, contracts, invariants, reachability, security,
reliability, observability signals) plus the groups that drive targeted
checks and artifact definitions.

Prerequisites (run in this order):
    infrahubctl schema load schema-library/base
    infrahubctl schema load schema-library/extensions/vrf \
        schema-library/extensions/cable \
        schema-library/extensions/location_minimal \
        schema-library/extensions/routing_bgp
    infrahubctl schema load schema-library/experimental/intent
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
        for name in ("Cisco Systems", "Arista Networks", "Nokia", "FRRouting")
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
    # selection. The Cisco image has no gNMI: it claims ssh_cli only, so
    # the observability compiler emits a CLI-scrape collector for it.
    # ------------------------------------------------------------------
    print("==> platforms")
    platforms = {}
    platform_defs = [
        ("cisco_iosxe", "Cisco Systems", "cisco_ios", "cisco_iol",
         ["ssh_cli", "snmp", "cli_config", "mpls", "vpn_ipv4"]),
        ("arista_eos", "Arista Networks", "arista_eos", "ceos",
         ["gnmi", "ssh_cli", "snmp", "cli_config", "mpls", "vpn_ipv4"]),
        ("nokia_srlinux", "Nokia", "nokia_srl", "nokia_srlinux",
         ["gnmi", "gnmi_set", "ssh_cli", "vpn_ipv4"]),
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
        "pe-emea-01": ("cisco_iosxe", "edge", as65010, pop_a, f"{MGMT_NET}.11", [
            ("Loopback0", "virtual", None, "10.255.0.1/32", "router-id"),
            ("Ethernet0/1", "physical", "core", "10.0.0.0/31", "to core-rr-01"),
            ("Ethernet0/2", "physical", "cust", "10.84.255.1/30", "to ce-custc-01 [customer-c]"),
            ("Ethernet0/3", "physical", "management", f"{OOB_NET}.11/24", "OOB"),
        ]),
        "pe-emea-02": ("arista_eos", "edge", as65010, pop_b, f"{MGMT_NET}.12", [
            ("Loopback0", "virtual", None, "10.255.0.2/32", "router-id"),
            ("Ethernet1", "physical", "core", "10.0.0.2/31", "to core-rr-01"),
            ("Ethernet2", "physical", "cust", "10.84.255.5/30", "to ce-custc-02 [customer-c]"),
            ("Ethernet3", "physical", "peering", "203.0.113.0/31", "to peer-inet-01"),
            ("Ethernet4", "physical", "management", f"{OOB_NET}.12/24", "OOB"),
        ]),
        "core-rr-01": ("nokia_srlinux", "core", as65010, pop_a, f"{MGMT_NET}.13", [
            ("system0", "virtual", None, "10.255.0.3/32", "router-id"),
            ("ethernet-1/1", "physical", "core", "10.0.0.1/31", "to pe-emea-01"),
            ("ethernet-1/2", "physical", "core", "10.0.0.3/31", "to pe-emea-02"),
            ("ethernet-1/10", "physical", "management", f"{OOB_NET}.13/24", "OOB"),
        ]),
        "ce-custc-01": ("frr", "cpe", as65123, pop_a, f"{MGMT_NET}.21", [
            ("eth1", "physical", "cust", "10.84.255.2/30", "to pe-emea-01"),
            ("eth2", "physical", "management", f"{OOB_NET}.21/24", "OOB"),
        ]),
        "ce-custc-02": ("frr", "cpe", as65123, pop_b, f"{MGMT_NET}.22", [
            ("eth1", "physical", "cust", "10.84.255.6/30", "to pe-emea-02"),
            ("eth2", "physical", "management", f"{OOB_NET}.22/24", "OOB"),
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
        (("pe-emea-01", "Ethernet0/1"), ("core-rr-01", "ethernet-1/1")),
        (("pe-emea-02", "Ethernet1"), ("core-rr-01", "ethernet-1/2")),
        (("ce-custc-01", "eth1"), ("pe-emea-01", "Ethernet0/2")),
        (("ce-custc-02", "eth1"), ("pe-emea-02", "Ethernet2")),
        (("peer-inet-01", "eth1"), ("pe-emea-02", "Ethernet3")),
        # the OOB plane - this is what makes the oob_reachability check pass
        (("pe-emea-01", "Ethernet0/3"), ("oob-sw-01", "eth1")),
        (("pe-emea-02", "Ethernet4"), ("oob-sw-01", "eth2")),
        (("core-rr-01", "ethernet-1/10"), ("oob-sw-01", "eth3")),
        (("ce-custc-01", "eth2"), ("oob-sw-01", "eth4")),
        (("ce-custc-02", "eth2"), ("oob-sw-01", "eth5")),
        (("peer-inet-01", "eth2"), ("oob-sw-01", "eth6")),
    ]
    for end_a, end_b in cables:
        upsert(
            client, "DcimCable",
            status="connected",
            connected_endpoints=[interfaces[end_a].id, interfaces[end_b].id],
        )

    # ------------------------------------------------------------------
    # Intent layer
    # ------------------------------------------------------------------
    print("==> tenant + contracts + invariants")
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

    contract_ce = upsert(
        client, "IntentRoutingContract",
        name="custc-ce-to-pe",
        description="Customer C CE handoff into CUSTC-PROD",
        role="customer_edge",
        peer_asn=65123,
        afi_safis=["ipv4-unicast", "ipv6-unicast"],
        max_prefixes_ipv4=200,
        max_prefixes_ipv6=100,
        local_preference=120,
        require_communities=["65010:1203", "65010:30010"],
        attach_communities=["65010:55555"],
        zone=zones["corp-emea"].id,
        tenant=tenant.id,
        pe_devices=[devices["pe-emea-01"].id, devices["pe-emea-02"].id],
        ce_devices=[devices["ce-custc-01"].id, devices["ce-custc-02"].id],
        allowed_prefixes=[
            prefixes["10.84.0.0/16"].id,
            prefixes["10.84.20.0/24"].id,
            prefixes["2001:db8:84::/48"].id,
        ],
        export_deny_zones=[zones["internet-peers"].id, zones["transit"].id],
    )
    upsert(client, "IntentInvariant", contract=contract_ce.id,
           invariant_type="no_leak",
           description="Customer C routes must not be exported to any public peer or transit")
    upsert(client, "IntentInvariant", contract=contract_ce.id,
           invariant_type="no_default_origination",
           description="Do not originate default into the CE unless explicitly enabled")

    contract_inet = upsert(
        client, "IntentRoutingContract",
        name="inet-peering-emea",
        description="Public peering with InetPeerCo - customer-c must NEVER appear here",
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

    contract_core = upsert(
        client, "IntentRoutingContract",
        name="ibgp-core-rr",
        description="vpn-ipv4 route reflection - PEs are clients of core-rr-01",
        role="core",
        peer_asn=65010,
        afi_safis=["vpn-ipv4"],
        zone=zones["core"].id,
        pe_devices=[devices["core-rr-01"].id],
        ce_devices=[devices["pe-emea-01"].id, devices["pe-emea-02"].id],
    )

    print("==> reachability / security / reliability")
    upsert(
        client, "IntentReachability",
        name="custc-apps-reachable-from-emea-corp",
        tenant=tenant.id,
        from_zone=zones["corp-emea"].id,
        to_prefixes=[prefixes["10.84.0.0/16"].id, prefixes["2001:db8:84::/48"].id],
        require_redundancy=True,
        max_convergence_seconds=30,
    )

    policy = upsert(
        client, "IntentSecurityPolicy",
        name="custc-inbound-protect",
        tenant=tenant.id,
        ddos_profile="standard_l3vpn",
        attach_device_role="pe",
        attach_interface_role="cust",
    )
    upsert(client, "IntentSecurityRule", policy=policy.id,
           name="allow-corp-https", index=10, action="allow", protocol="tcp",
           dst_ports=[443, 8443], src_zone=zones["corp-emea"].id,
           dst_prefixes=[prefixes["10.84.20.0/24"].id])
    upsert(client, "IntentSecurityRule", policy=policy.id,
           name="deny-internet-to-tenant", index=20, action="deny", protocol="any",
           src_zone=zones["internet-peers"].id,
           dst_prefixes=[prefixes["10.84.0.0/16"].id])

    upsert(
        client, "IntentReliability",
        name="custc-reliability",
        tenant=tenant.id,
        min_pe_attachments=2,
        require_distinct_failure_domains=True,
    )

    print("==> observability signals")
    upsert(client, "IntentObservabilitySignal",
           name="custc-bgp-session-state", signal="bgp_session_state",
           frequency_seconds=10, severity="sev2", condition="down_for > 60s",
           contract=contract_ce.id, tenant=tenant.id)
    upsert(client, "IntentObservabilitySignal",
           name="custc-bgp-route-count", signal="bgp_route_count",
           direction="inbound", frequency_seconds=30, severity="sev2",
           condition="count > max_prefixes",
           contract=contract_ce.id, tenant=tenant.id)
    upsert(client, "IntentObservabilitySignal",
           name="inet-peer-export-violation", signal="policy_export_violation",
           direction="outbound", frequency_seconds=30, severity="sev1",
           condition="count > 5",
           contract=contract_inet.id, tenant=tenant.id)
    upsert(client, "IntentObservabilitySignal",
           name="fleet-oob-reachability", signal="device_reachability",
           frequency_seconds=60, severity="sev3", condition="loss > 50%")

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
    upsert(client, "CoreStandardGroup", name="intent_contracts",
           members=[contract_ce.id, contract_inet.id, contract_core.id])
    upsert(client, "CoreStandardGroup", name="intent_tenants", members=[tenant.id])

    print("done. Open a Proposed Change and watch the invariants run.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # surface SDK errors with context
        print(f"bootstrap failed: {exc}", file=sys.stderr)
        raise
