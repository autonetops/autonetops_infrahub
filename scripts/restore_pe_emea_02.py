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
* peer-inet-01 (FRR, AS64999) and its eth1
* links toward core-rr-01, ce-custc-02 and peer-inet-01 - no cables
  (DcimCable is gone): the /31 and /30 addresses above ARE the links,
  see transforms/topology.py
* contract membership: ibgp-core-rr (RR client), custc-ce-to-pe
  (inet-peering-emea was retired 2026-11-22)
* groups: network_devices, monitored_devices

Idempotent: every node upserts on its unique key.

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

    print("==> contracts")
    core = get("IntentRoutingContract", name__value="ibgp-core-rr")
    add_rel(core, "ce_devices", pe.id)
    edge = get("IntentRoutingContract", name__value="custc-ce-to-pe")
    add_rel(edge, "pe_devices", pe.id)

    print("==> groups")
    add_rel(get("CoreStandardGroup", name__value="network_devices"),
            "members", pe.id, peer.id)
    add_rel(get("CoreStandardGroup", name__value="monitored_devices"),
            "members", pe.id, peer.id)

    print(f"done (branch {BRANCH})")


if __name__ == "__main__":
    main()
