#!/usr/bin/env python3
"""Sync one PE's IpamVRFInstance records from its running config (IOS / IOS-XE).

Brownfield onboarding for the per-PE RD model: reads which VRFs the router
runs and which routed interfaces sit in them, then upserts one
IpamVRFInstance per VRF on that device and binds the interfaces. The RD is
NOT copied from the box - the vrf_rd generator owns it - but the device's RD
is printed next to the one the generator will write, so drift shows up.

Skipped, with a note:
* VRFs unknown to the SoT, or without IpamVRF.vrf_id (e.g. `oob`: a local
  management VRF has no RD and no instance);
* interfaces bound on the box but missing in the SoT.

Read-only on the device (show commands). Dry run unless --apply.

    export CLI_USERNAME=cisco CLI_PASSWORD=...           # device credentials
    export INFRAHUB_ADDRESS=http://localhost:8000 INFRAHUB_API_TOKEN=...
    python scripts/vrf_instances_from_device.py --device DC02-PE-LL3-02 \\
        --host 10.10.10.128 --branch restore-pe-emea-02 [--apply]
"""

import argparse
import ipaddress
import os
import re
import sys

from infrahub_sdk import Config, InfrahubClientSync
from netmiko import ConnectHandler

QUERY = """
query ($device: String!) {
  DcimDevice(name__value: $device) {
    edges { node { id
      interfaces { edges { node { id name { value }
        ... on InterfaceLayer3 { ip_addresses { edges { node { address { value } } } } } } } }
      vrf_instances { edges { node { id route_distinguisher { value } rd_mode { value }
        vrf { node { name { value } } }
        interfaces { edges { node { id } } } } } } } }
  }
  IpamVRF { edges { node { id name { value } vrf_id { value } } } }
}
"""


def read_device(host, device_type):
    conn = ConnectHandler(
        device_type=device_type, host=host,
        username=os.environ["CLI_USERNAME"], password=os.environ["CLI_PASSWORD"],
        timeout=30,
    )
    try:
        vrfs = conn.send_command("show running-config | section ^vrf definition")
        ifaces = conn.send_command("show running-config | section ^interface")
    finally:
        conn.disconnect()

    device_rd = {}  # vrf -> rd on the box
    current = None
    for line in vrfs.splitlines():
        if m := re.match(r"^vrf definition (\S+)", line):
            current = m[1]
            device_rd[current] = None
        elif current and (m := re.match(r"^\s+rd (\S+)", line)):
            device_rd[current] = m[1]

    bindings = {}  # interface -> vrf
    current = None
    for line in ifaces.splitlines():
        if m := re.match(r"^interface (\S+)", line):
            current = m[1]
        elif current and (m := re.match(r"^\s+(?:ip )?vrf forwarding (\S+)", line)):
            bindings[current] = m[1]
    return device_rd, bindings


def loopback_v4(interfaces):
    """Same choice as generators/vrf_rd.py: Loopback0 first."""
    loops = sorted(
        (i for i in interfaces if i["name"]["value"].lower().startswith(("loopback", "system", "lo"))),
        key=lambda i: (i["name"]["value"].lower() not in ("loopback0", "system0", "lo0", "lo"), i["name"]["value"].lower()),
    )
    for i in loops:
        for ip in (i.get("ip_addresses") or {"edges": []})["edges"]:
            addr = ipaddress.ip_interface(ip["node"]["address"]["value"])
            if addr.version == 4:
                return str(addr.ip)
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--device", required=True, help="DcimDevice name in Infrahub")
    ap.add_argument("--host", required=True, help="management address to SSH to")
    ap.add_argument("--branch", default=os.environ.get("INFRAHUB_BRANCH", "main"))
    ap.add_argument("--device-type", default="cisco_ios", help="netmiko device_type (default cisco_ios)")
    ap.add_argument("--apply", action="store_true", help="write to Infrahub (default: dry run)")
    args = ap.parse_args()

    client = InfrahubClientSync(
        address=os.environ.get("INFRAHUB_ADDRESS", "http://localhost:8000"),
        config=Config(api_token=os.environ.get("INFRAHUB_API_TOKEN")),
    )
    data = client.execute_graphql(query=QUERY, variables={"device": args.device}, branch_name=args.branch)
    devs = data["DcimDevice"]["edges"]
    if not devs:
        sys.exit(f"{args.device}: not found on branch {args.branch}")
    dev = devs[0]["node"]
    sot_ifaces = {i["node"]["name"]["value"]: i["node"] for i in dev["interfaces"]["edges"]}
    sot_vrfs = {v["node"]["name"]["value"]: v["node"] for v in data["IpamVRF"]["edges"]}
    existing = {
        i["node"]["vrf"]["node"]["name"]["value"]: i["node"] for i in dev["vrf_instances"]["edges"]
    }
    lo = loopback_v4([i for i in sot_ifaces.values()])

    device_rd, bindings = read_device(args.host, args.device_type)
    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"{args.device} ({args.host}) -> branch {args.branch} [{mode}]  loopback {lo}")

    for vrf_name in sorted(device_rd):
        vrf = sot_vrfs.get(vrf_name)
        if not vrf:
            print(f"  {vrf_name:10} skip: no IpamVRF of that name in the SoT")
            continue
        if vrf["vrf_id"]["value"] is None:
            print(f"  {vrf_name:10} skip: IpamVRF has no vrf_id (local VRF, no RD)")
            continue
        wanted_rd = f"{lo}:{vrf['vrf_id']['value']}" if lo else None
        box_rd = device_rd[vrf_name]
        drift = "" if box_rd == wanted_rd else f"   <-- device differs, generator will write {wanted_rd}"
        members, missing = [], []
        for ifname, bound in sorted(bindings.items()):
            if bound != vrf_name:
                continue
            (members if ifname in sot_ifaces else missing).append(ifname)

        inst = existing.get(vrf_name)
        have = {e["node"]["id"] for e in inst["interfaces"]["edges"]} if inst else set()
        want = {sot_ifaces[n]["id"] for n in members}
        action = "exists" if inst else "create"
        if inst and have != want:
            action = "update interfaces"
        print(f"  {vrf_name:10} {action:18} device rd {box_rd}  members {members or '-'}{drift}")
        for n in missing:
            print(f"  {'':10} note: {n} is in {vrf_name} on the box but not in the SoT - not bound")

        if not args.apply or (inst and have == want):
            continue
        if inst:
            obj = client.get(kind="IpamVRFInstance", id=inst["id"], branch=args.branch, include=["interfaces"])
            obj.interfaces.fetch()
            for p in list(obj.interfaces.peers):
                if p.id not in want:
                    obj.interfaces.remove(p.id)
            for i in want - have:
                obj.interfaces.add(i)
            obj.save()
        else:
            client.create(
                kind="IpamVRFInstance", branch=args.branch,
                vrf=vrf["id"], device=dev["id"], rd_mode="auto", interfaces=sorted(want),
            ).save()

    if not args.apply:
        print("dry run - re-run with --apply to write")


if __name__ == "__main__":
    main()
