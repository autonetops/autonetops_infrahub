#!/usr/bin/env python3
"""Move route distinguishers from IpamVRF (one per VRF) to IpamVRFInstance (one per PE).

Only what follows directly from data already in the SoT is migrated:

* ``IpamVRF.vrf_id``  <- the number after the colon of the deprecated
  ``vrf_rd`` / ``route_distinguisher`` (``10.245.245.251:183`` -> 183,
  ``65010:1203`` -> 1203); left alone when already set.
* an instance on the PE that **owns** the deprecated RD's IP (a Type-1 RD
  whose IP is some device's loopback - e.g. 10.245.245.251 = DC01-PE-LL3-01).
* an instance on every ``pe_devices`` member of a routing contract whose
  tenant owns the VRF (CUSTC-PROD on PE-EMEA-01/02).
* every VRF with a vrf_id joins the ``vrfs`` group (the vrf_rd generator target).

Instances are created with ``rd_mode=auto`` and no RD - run the vrf_rd
generator afterwards to fill them. The deprecated RD values are left in
place (ignored by every consumer). Other PEs that carry a VRF in the real
network (e.g. the LL3-02 / DC02 copies, see docs/design G-2) are not
guessable from the SoT; add their instances by hand.

Idempotent: instances upsert on (vrf, device), the group on its name.

    INFRAHUB_BRANCH=vrf-instances python scripts/migrate_vrf_instances.py
"""

import ipaddress
import os
import re

from infrahub_sdk import Config, InfrahubClientSync

BRANCH = os.environ.get("INFRAHUB_BRANCH", "main")
RD_RE = re.compile(r"^(?P<left>[^:]+):(?P<num>\d+)$")

QUERY = """
query {
  IpamVRF {
    edges { node { id name { value } vrf_id { value }
      vrf_rd { value } route_distinguisher { value } } }
  }
  IpamIPAddress {
    edges { node { address { value }
      interface { node { ... on DcimInterface { name { value }
        device { node { id name { value } } } } } } } }
  }
  IntentRoutingContract {
    edges { node { name { value }
      intent { node { tenant { node { vrfs { edges { node { id } } } } } } }
      pe_devices { edges { node { id name { value } } } } } }
  }
}
"""


def main():
    client = InfrahubClientSync(
        address=os.environ.get("INFRAHUB_ADDRESS", "http://localhost:8000"),
        config=Config(api_token=os.environ.get("INFRAHUB_API_TOKEN")),
    )
    data = client.execute_graphql(query=QUERY, branch_name=BRANCH)

    # loopback /32 -> owning device
    loopback_owner = {}
    for e in data["IpamIPAddress"]["edges"]:
        n = e["node"]
        iface = (n.get("interface") or {}).get("node")
        if not iface or "name" not in iface:
            continue
        if not iface["name"]["value"].lower().startswith(("loopback", "system", "lo")):
            continue
        dev = iface["device"]["node"]
        loopback_owner[str(ipaddress.ip_interface(n["address"]["value"]).ip)] = dev

    wanted = set()  # (vrf_id, device_id, why)
    vrf_names = {}
    group_members = []
    for e in data["IpamVRF"]["edges"]:
        n = e["node"]
        vrf_names[n["id"]] = n["name"]["value"]
        old_rd = n["vrf_rd"]["value"] or n["route_distinguisher"]["value"]
        m = RD_RE.match(old_rd or "")
        vrf_id = n["vrf_id"]["value"]
        if m and vrf_id is None:
            vrf_id = int(m["num"])
            obj = client.get(kind="IpamVRF", id=n["id"], branch=BRANCH)
            obj.vrf_id.value = vrf_id
            obj.save()
            print(f"{n['name']['value']}: vrf_id <- {vrf_id} (from RD {old_rd})")
        if vrf_id is not None:
            group_members.append(n["id"])
        if m and m["left"] in loopback_owner:
            dev = loopback_owner[m["left"]]
            wanted.add((n["id"], dev["id"], dev["name"]["value"], f"owns RD {old_rd}"))

    for e in data["IntentRoutingContract"]["edges"]:
        c = e["node"]
        tenant = ((c.get("intent") or {}).get("node") or {}).get("tenant", {}).get("node")
        if not tenant:
            continue
        for v in tenant["vrfs"]["edges"]:
            for pe in c["pe_devices"]["edges"]:
                pe = pe["node"]
                wanted.add((v["node"]["id"], pe["id"], pe["name"]["value"],
                            f"PE of contract {c['name']['value']}"))

    for vrf_id, dev_id, dev_name, why in sorted(wanted, key=lambda w: (vrf_names[w[0]], w[2])):
        existing = client.filters(
            kind="IpamVRFInstance", branch=BRANCH, vrf__ids=[vrf_id], device__ids=[dev_id]
        )
        if existing:
            print(f"{vrf_names[vrf_id]}@{dev_name}: exists")
            continue
        client.create(
            kind="IpamVRFInstance", branch=BRANCH,
            vrf=vrf_id, device=dev_id, rd_mode="auto",
        ).save()
        print(f"{vrf_names[vrf_id]}@{dev_name}: created ({why})")

    group = client.create(
        kind="CoreStandardGroup", branch=BRANCH, name="vrfs",
        description="IpamVRF targets of the vrf_rd generator",
    )
    group.save(allow_upsert=True)
    group = client.get(kind="CoreStandardGroup", name__value="vrfs", branch=BRANCH, include=["members"])
    group.members.fetch()
    have = {p.id for p in group.members.peers}
    missing = [m for m in group_members if m not in have]
    for m in missing:
        group.members.add(m)
    if missing:
        group.save()
    print(f"group vrfs: {len(have) + len(missing)} member(s), +{len(missing)}")


if __name__ == "__main__":
    main()
