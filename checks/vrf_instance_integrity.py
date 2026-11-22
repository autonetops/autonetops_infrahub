"""VRF instance integrity - the merge gate behind the per-PE RD model.

``IpamVRFRouteDistinguisher.route_distinguisher`` cannot carry ``unique: true``
(Infrahub counts empty values as equal, so two not-yet-generated instances
would collide), and the instance's ``interfaces`` peer cannot exclude
switchports (physical/virtual interfaces inherit both InterfaceLayer2 and
InterfaceLayer3). This check enforces both, network-wide:

* every RD is used by exactly one instance - a shared RD makes the route
  reflector collapse the two PEs' paths into one (no multipath, and the RD
  no longer names the originating PE);
* ``rd_mode: manual`` comes with an RD (manual = a human owns the value);
* members are routed interfaces only: no ``l2_mode`` (a switchport's VLAN
  reaches the VRF through its SVI, which is the member);
* a member belongs to the instance's own device;
* an interface is a member of at most one instance (an L3 interface
  forwards in exactly one VRF).

Global check (no targets): one run sees every instance in the branch.
"""

from collections import defaultdict

from infrahub_sdk.checks import InfrahubCheck


def _v(attr):
    return attr.get("value") if attr else None


def _node(rel):
    return rel.get("node") if rel else None


def _edges(rel):
    return [e["node"] for e in rel["edges"]] if rel else []


def _label(inst):
    vrf = _node(inst.get("vrf")) or {}
    dev = _node(inst.get("device")) or {}
    return f"{_v(vrf.get('name'))}@{_v(dev.get('name'))}"


def find_violations(instances):
    """Return (message, object_id) pairs - pure, so it is testable offline."""
    violations = []
    by_rd = defaultdict(list)
    iface_owner = defaultdict(list)  # interface id -> instance labels

    for inst in instances:
        label = _label(inst)
        rd = _v(inst.get("route_distinguisher"))
        if rd:
            by_rd[rd].append(label)
        elif _v(inst.get("rd_mode")) == "manual":
            violations.append((f"{label}: rd_mode is manual but no route_distinguisher is set", inst["id"]))

        device = _v((_node(inst.get("device")) or {}).get("name"))
        for iface in _edges(inst.get("interfaces")):
            name = _v(iface.get("name"))
            owner = _v((_node(iface.get("device")) or {}).get("name"))
            where = f"{owner}:{name}"
            iface_owner[iface["id"]].append((label, where))
            if _v(iface.get("l2_mode")):
                violations.append((
                    f"{label}: member {where} is a switchport (l2_mode={_v(iface['l2_mode'])}) - "
                    f"only routed interfaces belong to a VRF; add its SVI/sub-interface instead",
                    inst["id"],
                ))
            if owner != device:
                violations.append((
                    f"{label}: member {where} belongs to {owner}, not to the instance's device {device}",
                    inst["id"],
                ))

    for rd, labels in sorted(by_rd.items()):
        if len(labels) > 1:
            violations.append((f"RD {rd} is used by {len(labels)} instances: {', '.join(sorted(labels))}", None))

    for owners in iface_owner.values():
        if len(owners) > 1:
            where = owners[0][1]
            violations.append((
                f"{where} is a member of {len(owners)} VRF instances "
                f"({', '.join(sorted(o[0] for o in owners))}) - an L3 interface forwards in one VRF",
                None,
            ))
    return violations


class VrfInstanceIntegrityCheck(InfrahubCheck):
    query = "vrf_instance_integrity"

    def validate(self, data):
        instances = _edges(data.get("IpamVRFRouteDistinguisher"))
        violations = find_violations(instances)
        for message, object_id in violations:
            if object_id:
                self.log_error(message=message, object_id=object_id, object_type="IpamVRFRouteDistinguisher")
            else:
                self.log_error(message=message)
        if not violations:
            self.log_info(message=f"{len(instances)} VRF instance(s): RDs unique, members routed and single-VRF")
