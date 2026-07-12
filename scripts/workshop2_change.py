"""Workshop 2 change: customer-c expansion, on an Infrahub branch.

The business ask:
  1. Customer C activates a new site range 10.84.40.0/24 - it must be
     accepted from their CEs and advertised inside CUSTC-PROD.
  2. Their routes should win over any backup path: local-preference 150.
  3. Engineering wants a services loopback (Loopback1) on both PEs.

Everything happens on a branch - main stays the deployed truth until the
proposed change merges. Run me, review the branch in the UI, open the
proposed change, and let the orchestrator take it from there:

    python scripts/workshop2_change.py [--branch workshop2-cust-c-expansion]
    python scripts/workshop2_change.py --propose   # also open the PC

Idempotent: re-running updates the same objects on the same branch.
"""

import argparse
import os
import sys

from infrahub_sdk import Config, InfrahubClientSync

NEW_PREFIX = "10.84.40.0/24"
NEW_LOCAL_PREF = 150
CONTRACT = "custc-ce-to-pe"
LOOPBACKS = {
    "pe-emea-01": ("Loopback1", "10.255.10.1/32", "services anchor [workshop2]"),
    "pe-emea-02": ("Loopback1", "10.255.10.2/32", "services anchor [workshop2]"),
}

RELATIONSHIP_ADD = """
mutation AddPrefix($contract: String!, $prefix: String!) {
  RelationshipAdd(data: {
    id: $contract, name: "allowed_prefixes", nodes: [{id: $prefix}]
  }) { ok }
}
"""

PROPOSE = """
mutation Propose($name: String!, $source: String!) {
  CoreProposedChangeCreate(data: {
    name: {value: $name},
    source_branch: {value: $source},
    destination_branch: {value: "main"},
  }) { ok object { id } }
}
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branch", default="workshop2-cust-c-expansion")
    parser.add_argument("--propose", action="store_true",
                        help="also open the proposed change")
    args = parser.parse_args()

    client = InfrahubClientSync(
        address=os.environ.get("INFRAHUB_ADDRESS", "http://localhost:8000"),
        config=Config(api_token=os.environ.get("INFRAHUB_API_TOKEN")),
    )

    # -- the branch: an isolated fork of the whole graph -------------------
    existing = {b.name for b in client.branch.all().values()}
    if args.branch not in existing:
        client.branch.create(branch_name=args.branch, sync_with_git=False)
        print(f"branch {args.branch!r} created")
    else:
        print(f"branch {args.branch!r} already exists - updating it")

    def upsert(kind, **data):
        obj = client.create(kind=kind, branch=args.branch, **data)
        obj.save(allow_upsert=True)
        return obj

    # -- 1. the new advertisement ------------------------------------------
    vrf = client.get(kind="IpamVRF", name__value="CUSTC-PROD", branch=args.branch)
    prefix = upsert("IpamPrefix", prefix=NEW_PREFIX, status="active", vrf=vrf.id)
    contract = client.get(kind="IntentRoutingContract",
                          name__value=CONTRACT, branch=args.branch)
    client.execute_graphql(query=RELATIONSHIP_ADD, branch_name=args.branch,
                           variables={"contract": contract.id, "prefix": prefix.id})
    print(f"{NEW_PREFIX} added to {CONTRACT}.allowed_prefixes (VRF CUSTC-PROD)")

    # -- 2. the routing-policy change --------------------------------------
    contract.local_preference.value = NEW_LOCAL_PREF
    contract.save()
    print(f"{CONTRACT}.local_preference = {NEW_LOCAL_PREF}")

    # -- 3. the new interfaces ----------------------------------------------
    for device_name, (iface, ip, description) in LOOPBACKS.items():
        device = client.get(kind="DcimDevice", name__value=device_name,
                            branch=args.branch)
        addr = upsert("IpamIPAddress", address=ip)
        upsert("InterfaceVirtual", name=iface, device=device.id,
               status="active", description=description, ip_addresses=[addr.id])
        print(f"{device_name}: {iface} {ip} ({description})")

    # -- optionally, the proposed change ------------------------------------
    if args.propose:
        result = client.execute_graphql(query=PROPOSE, variables={
            "name": "workshop2: customer-c expansion",
            "source": args.branch,
        })
        pc_id = result["CoreProposedChangeCreate"]["object"]["id"]
        print(f"proposed change opened: {pc_id}")
        print("review it in the UI - the invariant checks gate the merge")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"workshop2 change failed: {exc}", file=sys.stderr)
        raise
