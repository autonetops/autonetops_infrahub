"""Per-PE VRF instances from IPAM: attachment interfaces + route distinguishers.

One run per member of the ``vrfs`` group (one VRF). Two things, in order:

1. **Attachments.** IPAM is the source: an interface holding an IP address
   in the VRF is an attachment of its device's ``IpamVRFRouteDistinguisher``.
   * missing binding -> added;
   * device with such an interface but no instance -> instance created
     (``rd_mode: auto``) - the PE carries the VRF because IPAM says so;
   * a bound interface whose addresses are all *outside* the VRF -> unbound
     (IPAM contradicts it). Bound interfaces without any address
     (unnumbered, not yet addressed) are left alone;
   * an address on a switchport is reported, never attached (VRF members
     are routed interfaces).
   Instances are never deleted: a PE may carry a VRF with no attachment
   (import-only), so removing one stays a human decision.

2. **RD.** ``route_distinguisher = <PE Loopback0 IPv4>:<IpamVRF.vrf_id>``
   (RFC 4364 Type 1) on every instance in ``rd_mode: auto``; ``manual``
   instances keep theirs but still count toward the distinctness check. One
   RD per PE - never one per VRF - so a site dual-homed to two PEs yields
   two distinct VPNv4 routes: the route reflector reflects both, remote PEs
   can multipath across them, and the RD's IP names the originating PE.

Tracking: generator runs are wrapped in ``start_tracking(delete_unused_nodes=True)``.
Every save opts out of the group context, so nothing this generator writes -
created instances included - is deleted by a later run.
"""

import ipaddress

from infrahub_sdk.generator import InfrahubGenerator

# same notion of "loopback" as transforms/device_config.py (LOOPBACK_PREFIXES)
LOOPBACK_PREFIXES = ("loopback", "system", "lo")
PREFERRED_LOOPBACKS = ("loopback0", "system0", "lo0", "lo")


def _v(attr):
    return attr.get("value") if attr else None


def _edges(rel):
    return [e["node"] for e in rel["edges"]] if rel else []


def loopback_ipv4(interfaces):
    """The PE's RD/router-id address: Loopback0 first, then the first other
    loopback-ish interface (by name) carrying an IPv4 address."""

    def v4(iface):
        for ip in _edges(iface.get("ip_addresses")):
            addr = ipaddress.ip_interface(_v(ip["address"]))
            if addr.version == 4:
                return str(addr.ip)
        return None

    candidates = [
        i for i in interfaces
        if (_v(i["name"]) or "").lower().startswith(LOOPBACK_PREFIXES)
    ]
    candidates.sort(key=lambda i: (
        (_v(i["name"]) or "").lower() not in PREFERRED_LOOPBACKS,
        (_v(i["name"]) or "").lower(),
    ))
    for iface in candidates:
        ip = v4(iface)
        if ip:
            return ip
    return None


def derive_rd(loopback, vrf_id):
    return f"{loopback}:{vrf_id}"


def _node(rel):
    return rel.get("node") if rel else None


def plan(data):
    """Everything one run should change for one VRF - pure, testable offline.

    Returns ``(vrf, records, notes)``. Each record is one instance (existing,
    or ``id`` None = to create) with its target RD and the interfaces to
    bind/unbind (id -> name). Raises ValueError on a state the generator
    must not write (no vrf_id, PE without loopback, two PEs on one RD).
    """
    vrfs = _edges(data["IpamVRF"])
    if not vrfs:
        raise ValueError("target VRF not found")
    vrf = vrfs[0]
    name, vrf_id = _v(vrf["name"]), _v(vrf["vrf_id"])
    notes = []

    # where IPAM says the VRF is attached: device id -> {device, ifaces}
    attach = {}
    for ip in _edges(data.get("IpamIPAddress")):
        iface = _node(ip.get("interface"))
        if not iface or "device" not in iface:
            continue  # unassigned address
        dev = iface["device"]["node"]
        label = f"{_v(dev['name'])}:{_v(iface['name'])}"
        if _v(iface.get("l2_mode")):
            notes.append(f"{label} holds {_v(ip['address'])} in {name} but is a switchport - not attached")
            continue
        entry = attach.setdefault(dev["id"], {"device": dev, "ifaces": {}})
        entry["ifaces"][iface["id"]] = _v(iface["name"])

    records = []
    for inst in _edges(vrf.get("instances")):
        dev = inst["device"]["node"]
        derived = attach.pop(dev["id"], {}).get("ifaces", {})
        bound = {i["id"]: i for i in _edges(inst.get("interfaces"))}
        remove = {}
        for iid, iface in bound.items():
            if iid in derived:
                continue
            addr_vrfs = {
                _v((_node(a.get("vrf")) or {}).get("name"))
                for a in _edges(iface.get("ip_addresses"))
            }
            if addr_vrfs and name not in addr_vrfs:
                remove[iid] = _v(iface.get("name"))
        records.append({
            "id": inst["id"],
            "device": dev,
            "rd_mode": _v(inst["rd_mode"]),
            "current": _v(inst["route_distinguisher"]),
            "add": {i: n for i, n in derived.items() if i not in bound},
            "remove": remove,
        })
    for dev_id, entry in attach.items():  # IPAM attachments on PEs without an instance
        records.append({
            "id": None,
            "device": entry["device"],
            "rd_mode": "auto",
            "current": None,
            "add": entry["ifaces"],
            "remove": {},
        })

    if records and vrf_id is None:
        raise ValueError(f"{name}: vrf_id is not set - cannot derive the RD of {len(records)} instance(s)")

    problems = []
    for rec in records:
        dev_name = _v(rec["device"]["name"])
        if rec["rd_mode"] == "manual":
            rec["rd"] = rec["current"]
            continue
        lo = loopback_ipv4(_edges(rec["device"].get("interfaces")))
        if not lo:
            problems.append(f"{dev_name}: no loopback with an IPv4 address")
            continue
        rec["rd"] = derive_rd(lo, vrf_id)

    # two PEs of one VRF on the same RD defeats the whole model
    seen = {}
    for rec in records:
        rd, dev_name = rec.get("rd"), _v(rec["device"]["name"])
        if rd and rd in seen:
            problems.append(f"RD {rd} on both {seen[rd]} and {dev_name}")
        seen.setdefault(rd, dev_name)
    if problems:
        raise ValueError(f"{name}: " + "; ".join(problems))
    return vrf, records, notes


class VrfRdGenerator(InfrahubGenerator):
    async def generate(self, data: dict) -> None:
        vrf, records, notes = plan(data)
        name = _v(vrf["name"])
        for note in notes:
            self.logger.warning(f"{name}: {note}")
        if not records:
            self.logger.info(f"{name}: no instances and no addresses in IPAM; nothing to do.")
            return

        for rec in records:
            label = f"{name}@{_v(rec['device']['name'])}"
            ifaces = ", ".join(sorted(rec["add"].values())) or "-"
            if rec["id"] is None:
                obj = await self.client.create(
                    kind="IpamVRFRouteDistinguisher", branch=self.branch_name,
                    vrf=vrf["id"], device=rec["device"]["id"], rd_mode="auto",
                    route_distinguisher=rec["rd"], interfaces=sorted(rec["add"]),
                )
                await obj.save(update_group_context=False)
                self.logger.info(f"{label}: created, rd {rec['rd']}, interfaces {ifaces}")
                continue

            changes = []
            if rec["current"] != rec["rd"]:
                changes.append(f"rd {rec['current']} -> {rec['rd']}")
            if rec["add"]:
                changes.append(f"+ {ifaces}")
            if rec["remove"]:
                changes.append("- " + ", ".join(sorted(rec["remove"].values())) + " (IPAM: not in this VRF)")
            if not changes:
                self.logger.info(f"{label}: rd {rec['rd']} (unchanged)")
                continue
            obj = await self.client.get(
                kind="IpamVRFRouteDistinguisher", id=rec["id"], branch=self.branch_name, include=["interfaces"]
            )
            obj.route_distinguisher.value = rec["rd"]
            for iid in rec["add"]:
                obj.interfaces.add(iid)
            for iid in rec["remove"]:
                obj.interfaces.remove(iid)
            await obj.save(update_group_context=False)
            self.logger.info(f"{label}: " + "; ".join(changes))
