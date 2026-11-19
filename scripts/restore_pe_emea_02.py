#!/usr/bin/env python3
"""Re-attach PE-EMEA-02's intent subgraph after the device was recreated.

PE-EMEA-02 was deleted and re-added by hand. The delete cascaded through
everything the device owned or anchored, so the new node rendered as an
empty shell (``router bgp None``, no interfaces) and dropped out of the
``network_devices`` artifact target. ``peer-inet-01`` went with it.

What this restores - exactly the model ``bootstrap.py`` seeds, adapted to
the upper-case device names now in the SoT:

* ASN 65010 and site EMEA POP B on PE-EMEA-02
* Loopback0 / Ethernet1-4, re-bound to the (orphaned, still present) IPs
* the half-empty cables toward core-rr-01 Ethernet0/2 and ce-custc-02
  ethernet-1/1
* peer-inet-01 (FRR, AS64999) and its peering cable to Ethernet3
* contract membership: ibgp-core-rr (RR client), inet-peering-emea (+ its
  198.51.100.0/24 allowed prefix)
* groups: network_devices, monitored_devices

Idempotent: every node upserts on its unique key; cables are only touched
when an endpoint is missing.

    INFRAHUB_BRANCH=restore-pe-emea-02 python scripts/restore_pe_emea_02.py
"""

import os

from infrahub_sdk import Config, InfrahubClientSync

BRANCH = os.environ.get("INFRAHUB_BRANCH", "main")
PE = "PE-EMEA-02"
PEER = "peer-inet-01"

PE_IFACES = [
    # name, kind, role, ip, description (live device descriptions)
    ("Loopback0", "InterfaceVirtual", None, "10.255.0.2/32", "router-id"),
    ("Ethernet1", "InterfacePhysical", "core", "10.100.0.2/31", "to core-rr-01"),
    ("Ethernet2", "InterfacePhysical", "cust", "10.84.255.5/30", "to ce-custc-02 [customer-c]"),
    ("Ethernet3", "InterfacePhysical", "peering", "203.0.113.0/31", "to peer-inet-01"),
    ("Ethernet4", "InterfacePhysical", "management", "192.168.100.12/24", "OOB"),
]
PEER_IFACES = [
    ("eth1", "InterfacePhysical", "peering", "203.0.113.1/31", "to pe-emea-02"),
]


def main():
    client = InfrahubClientSync(
        address=os.environ.get("INFRAHUB_ADDRESS", "http://localhost:8000"),
        config=Config(api_token=os.environ.get("INFRAHUB_API_TOKEN")),
    )

    def upsert(kind, **data):
        obj = client.create(kind=kind, branch=BRANCH, **data)
        obj.save(allow_upsert=True)
        return obj

    def get(kind, **filters):
        return client.get(kind=kind, branch=BRANCH, **filters)

    def add_rel(node, rel, *ids):
        node = client.get(kind=node.get_kind(), id=node.id, branch=BRANCH,
                          include=[rel])
        manager = getattr(node, rel)
        have = {p.id for p in manager.peers}
        missing = [i for i in ids if i not in have]
        for i in missing:
            manager.add(i)
        if missing:
            node.save()
        return bool(missing)

    as65010 = get("RoutingAutonomousSystem", asn__value=65010)
    as64999 = get("RoutingAutonomousSystem", asn__value=64999)
    pop_b = get("LocationSite", name__value="EMEA POP B")
    frr = get("DcimPlatform", name__value="frr")

    print(f"==> {PE}: asn + site")
    pe = get("DcimDevice", name__value=PE)
    pe.asn = as65010.id
    pe.location = pop_b.id
    pe.save()

    print(f"==> {PEER}: device")
    peer_mgmt = upsert("IpamIPAddress", address="172.20.20.31/24")
    peer = upsert(
        "DcimDevice", name=PEER, status="active", role="edge",
        platform=frr.id, location=pop_b.id, primary_address=peer_mgmt.id,
        asn=as64999.id,
    )

    def build_ifaces(device, defs):
        out = {}
        for name, kind, role, ip, description in defs:
            data = {"name": name, "device": device.id, "status": "active",
                    "description": description}
            if role:
                data["role"] = role
            if ip:
                data["ip_addresses"] = [upsert("IpamIPAddress", address=ip).id]
            out[name] = upsert(kind, **data)
            print(f"    {device.name.value} {name} {ip or ''}")
        return out

    print("==> interfaces")
    pe_if = build_ifaces(pe, PE_IFACES)
    peer_if = build_ifaces(peer, PEER_IFACES)

    print("==> cabling")
    cables = client.all("DcimCable", branch=BRANCH, include=["connected_endpoints"])

    def endpoint_ids(cable):
        # peer ids are populated on query; no fetch needed (and a fetch
        # fails on peers this run just added by id only)
        return {p.id for p in cable.connected_endpoints.peers}

    def iface_id(device, name):
        for kind in ("InterfacePhysical", "InterfaceVirtual"):
            hits = client.filters(kind=kind, branch=BRANCH,
                                  device__name__value=device, name__value=name)
            if hits:
                return hits[0].id
        raise LookupError(f"{device} {name} not found")

    def cable_to(far_dev, far_iface, local_id):
        far_id = iface_id(far_dev, far_iface)
        for cable in cables:
            ends = endpoint_ids(cable)
            if local_id in ends:
                print(f"    {far_dev} {far_iface}: already cabled")
                return
            if far_id in ends:
                add_rel(cable, "connected_endpoints", local_id)
                print(f"    {far_dev} {far_iface}: re-attached orphaned cable")
                return
        upsert("DcimCable", status="connected", connected_endpoints=[far_id, local_id])
        print(f"    {far_dev} {far_iface}: new cable")

    cable_to("core-rr-01", "Ethernet0/2", pe_if["Ethernet1"].id)
    cable_to("ce-custc-02", "ethernet-1/1", pe_if["Ethernet2"].id)
    cable_to(PE, "Ethernet3", peer_if["eth1"].id)

    print("==> contracts")
    core = get("IntentRoutingContract", name__value="ibgp-core-rr")
    add_rel(core, "ce_devices", pe.id)
    edge = get("IntentRoutingContract", name__value="custc-ce-to-pe")
    add_rel(edge, "pe_devices", pe.id)
    inet = get("IntentRoutingContract", name__value="inet-peering-emea")
    add_rel(inet, "pe_devices", pe.id)
    add_rel(inet, "ce_devices", peer.id)
    # the peer's authorized announcement went with the cascade too
    peer_pfx = upsert("IpamPrefix", prefix="198.51.100.0/24", status="active")
    add_rel(inet, "allowed_prefixes", peer_pfx.id)

    print("==> groups")
    add_rel(get("CoreStandardGroup", name__value="network_devices"),
            "members", pe.id, peer.id)
    add_rel(get("CoreStandardGroup", name__value="monitored_devices"),
            "members", pe.id, peer.id)

    print(f"done (branch {BRANCH})")


if __name__ == "__main__":
    main()
