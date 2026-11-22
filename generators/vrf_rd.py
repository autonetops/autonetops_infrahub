"""Write the per-PE route distinguisher of every instance of one VRF.

Rule: ``IpamVRFInstance.route_distinguisher = <PE Loopback0 IPv4>:<IpamVRF.vrf_id>``
(RFC 4364 Type 1). One RD per PE - never one per VRF - so a site dual-homed
to two PEs yields two distinct VPNv4 routes: the route reflector reflects
both, remote PEs can multipath across them, and the RD's IP names the
originating PE.

Runs once per member of the ``vrfs`` group. Instances whose ``rd_mode`` is
``manual`` are left untouched (but still count toward the distinctness
check). The generator never creates or deletes instances - which PEs carry a
VRF is data a human (or a later generator) owns.

Tracking: generator runs are wrapped in ``start_tracking(delete_unused_nodes=True)``.
The instances are pre-existing objects the generator merely edits, so every
save opts out of the group context - otherwise a later run that skips an
instance (manual mode, missing loopback) would delete it.
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


class VrfRdGenerator(InfrahubGenerator):
    async def generate(self, data: dict) -> None:
        vrfs = _edges(data["IpamVRF"])
        if not vrfs:
            raise ValueError(f"Target VRF not found: {self.params}")
        vrf = vrfs[0]
        name, vrf_id = _v(vrf["name"]), _v(vrf["vrf_id"])
        instances = _edges(vrf.get("instances"))
        if not instances:
            self.logger.info(f"{name}: no instances; nothing to do.")
            return
        if vrf_id is None:
            raise ValueError(
                f"{name}: vrf_id is not set - cannot derive the RD of its "
                f"{len(instances)} instance(s)"
            )

        wanted = {}  # instance id -> (device, current rd, target rd)
        problems = []
        for inst in instances:
            device = inst["device"]["node"]
            dev_name = _v(device["name"])
            current = _v(inst["route_distinguisher"])
            if _v(inst["rd_mode"]) == "manual":
                wanted[inst["id"]] = (dev_name, current, current)
                continue
            lo = loopback_ipv4(_edges(device.get("interfaces")))
            if not lo:
                problems.append(f"{dev_name}: no loopback with an IPv4 address")
                continue
            wanted[inst["id"]] = (dev_name, current, derive_rd(lo, vrf_id))

        # two PEs of one VRF on the same RD defeats the whole model
        seen = {}
        for dev_name, _cur, rd in wanted.values():
            if rd and rd in seen:
                problems.append(f"RD {rd} on both {seen[rd]} and {dev_name}")
            seen.setdefault(rd, dev_name)
        if problems:
            raise ValueError(f"{name}: " + "; ".join(problems))

        for inst_id, (dev_name, current, rd) in wanted.items():
            if current == rd:
                self.logger.info(f"{name}@{dev_name}: rd {rd} (unchanged)")
                continue
            obj = await self.client.get(kind="IpamVRFInstance", id=inst_id, branch=self.branch_name)
            obj.route_distinguisher.value = rd
            await obj.save(update_group_context=False)
            self.logger.info(f"{name}@{dev_name}: rd {current} -> {rd}")
