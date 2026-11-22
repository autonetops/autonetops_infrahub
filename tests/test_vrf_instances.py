"""Offline tests for per-PE VRF instances (no Infrahub needed).

generators/vrf_rd.py derives RD = <PE Loopback0>:<vrf_id>;
transforms/device_config.py renders each PE's own RD and the explicit
interface binding; checks/vrf_instance_integrity.py gates RD uniqueness and
membership (routed, own device, one VRF). The lab data only exercises the
contract-derived path, so these cover explicit bindings, manual RDs and the
failure modes.
"""

import asyncio
import logging
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from checks.vrf_instance_integrity import find_violations  # noqa: E402
from generators.vrf_rd import VrfRdGenerator, derive_rd, loopback_ipv4, plan  # noqa: E402
from transforms.device_config import (  # noqa: E402
    interface_vrf,
    parse_vrf_instances,
    vrf_rd,
)


def v(value):
    return {"value": value}


def edges(*nodes):
    return {"edges": [{"node": n} for n in nodes]}


def iface(name, *ips):
    return {"name": v(name), "ip_addresses": edges(*[{"address": v(ip)} for ip in ips])}


# --- generator ----------------------------------------------------------

def test_loopback0_preferred_over_other_loopbacks():
    ifaces = [
        iface("Ethernet1", "10.0.0.1/31"),
        iface("Loopback10", "192.0.2.10/32"),
        iface("Loopback0", "2001:db8::1/128", "10.255.0.1/32"),
    ]
    assert loopback_ipv4(ifaces) == "10.255.0.1"


def test_no_ipv4_loopback():
    assert loopback_ipv4([iface("Loopback0", "2001:db8::1/128"), iface("Ethernet1", "10.0.0.1/31")]) is None


def test_derive_rd():
    assert derive_rd("10.255.0.2", 1203) == "10.255.0.2:1203"


class _Gen(VrfRdGenerator):
    def __init__(self):  # skip the SDK plumbing - generate() only needs a logger
        self.params = {}
        self.logger = logging.getLogger("test")


def _vrf(vrf_id, *instances, ips=()):
    return {
        "IpamVRF": edges({"id": "vrf-s100", "name": v("s100"), "vrf_id": v(vrf_id), "instances": edges(*instances)}),
        "IpamIPAddress": edges(*ips),
    }


def _dev(name, lo):
    return {"id": f"dev-{name}", "name": v(name), "interfaces": edges(iface("Loopback0", lo))}


def _inst(dev, lo, rd=None, mode="auto", bound=()):
    """bound: (iface id, name, [vrf names of its addresses])"""
    return {
        "id": f"inst-{dev}",
        "route_distinguisher": v(rd),
        "rd_mode": v(mode),
        "interfaces": edges(*[
            {"id": i, "name": v(n), "ip_addresses": edges(*[{"vrf": {"node": {"name": v(x)}} if x else None} for x in vrfs])}
            for i, n, vrfs in bound
        ]),
        "device": {"node": _dev(dev, lo)},
    }


def _ip(addr, dev, lo, iface_id, iface_name, l2_mode=None):
    """An IpamIPAddress in the VRF, on interface iface_name of dev."""
    return {
        "address": v(addr),
        "interface": {"node": {"id": iface_id, "name": v(iface_name), "l2_mode": v(l2_mode), "device": {"node": _dev(dev, lo)}}},
    }


def test_generator_rejects_shared_rd():
    # PE-B pinned (manual) to PE-A's derived RD -> both would advertise 10.0.0.1:100
    data = _vrf(100, _inst("PE-A", "10.0.0.1/32"), _inst("PE-B", "10.0.0.2/32", rd="10.0.0.1:100", mode="manual"))
    with pytest.raises(ValueError, match="10.0.0.1:100 on both"):
        asyncio.run(_Gen().generate(data))


def test_generator_requires_vrf_id():
    with pytest.raises(ValueError, match="vrf_id is not set"):
        asyncio.run(_Gen().generate(_vrf(None, _inst("PE-A", "10.0.0.1/32"))))


def test_generator_noop_when_up_to_date():
    # nothing to save -> no client needed
    data = _vrf(100, _inst("PE-A", "10.0.0.1/32", rd="10.0.0.1:100"),
                _inst("PE-B", "10.0.0.2/32", rd="65000:7", mode="manual"))
    asyncio.run(_Gen().generate(data))


def _by_dev(records):
    return {r["device"]["name"]["value"]: r for r in records}


def test_plan_binds_ipam_attachment_to_existing_instance():
    data = _vrf(100, _inst("PE-A", "10.0.0.1/32", rd="10.0.0.1:100"),
                ips=[_ip("172.16.0.1/29", "PE-A", "10.0.0.1/32", "a-vl100", "Vlan100")])
    _vrf_node, records, notes = plan(data)
    rec = _by_dev(records)["PE-A"]
    assert (rec["id"], rec["add"], rec["remove"], rec["rd"]) == ("inst-PE-A", {"a-vl100": "Vlan100"}, {}, "10.0.0.1:100")
    assert notes == []


def test_plan_creates_instance_where_ipam_attaches_the_vrf():
    data = _vrf(100, ips=[_ip("172.16.0.2/29", "RR-1", "10.0.0.9/32", "r-vl100", "Vlan100")])
    _vrf_node, (rec,), _ = plan(data)
    assert rec["id"] is None
    assert rec["add"] == {"r-vl100": "Vlan100"}
    assert rec["rd"] == "10.0.0.9:100"


def test_plan_unbinds_only_what_ipam_contradicts():
    data = _vrf(100, _inst("PE-A", "10.0.0.1/32", rd="10.0.0.1:100", bound=[
        ("a-moved", "Gi0/2", ["v183"]),    # its address now lives in another VRF -> unbind
        ("a-global", "Gi0/3", [None]),     # address in the global table -> unbind
        ("a-unnum", "Tunnel9", []),        # no address at all -> keep
        ("a-ok", "Vlan100", ["s100"]),     # still in this VRF -> keep
    ]), ips=[_ip("172.16.0.1/29", "PE-A", "10.0.0.1/32", "a-ok", "Vlan100")])
    rec = _by_dev(plan(data)[1])["PE-A"]
    assert rec["remove"] == {"a-moved": "Gi0/2", "a-global": "Gi0/3"}
    assert rec["add"] == {}


def test_plan_never_attaches_a_switchport():
    data = _vrf(100, ips=[_ip("172.16.0.3/29", "SW-1", "10.0.0.8/32", "s-e1", "Ethernet1", l2_mode="access")])
    _vrf_node, records, notes = plan(data)
    assert records == []
    assert "SW-1:Ethernet1" in notes[0] and "switchport" in notes[0]


def test_plan_needs_vrf_id_once_ipam_attaches_it():
    with pytest.raises(ValueError, match="vrf_id is not set"):
        plan(_vrf(None, ips=[_ip("172.16.0.1/29", "PE-A", "10.0.0.1/32", "a1", "Vlan100")]))


class _FakeNode:
    def __init__(self, store, **data):
        self.store, self.data = store, data

    async def save(self, update_group_context=True):
        assert update_group_context is False  # never tracked -> never auto-deleted
        self.store.append(self.data)


class _FakeClient:
    def __init__(self):
        self.created = []

    async def create(self, kind, branch=None, **data):
        return _FakeNode(self.created, kind=kind, **data)


def test_generate_creates_untracked_instance_with_rd_and_interfaces():
    gen = _Gen()
    gen.client, gen.branch = _FakeClient(), "main"
    asyncio.run(gen.generate(_vrf(100, ips=[_ip("172.16.0.2/29", "RR-1", "10.0.0.9/32", "r-vl100", "Vlan100")])))
    assert gen.client.created == [{
        "kind": "IpamVRFRouteDistinguisher", "vrf": "vrf-s100", "device": "dev-RR-1", "rd_mode": "auto",
        "route_distinguisher": "10.0.0.9:100", "interfaces": ["r-vl100"],
    }]


# --- transform ----------------------------------------------------------

def _device_node():
    return {
        "vrf_instances": edges(
            {
                "route_distinguisher": v("10.255.0.1:100"),
                "vrf": {"node": {"name": v("s100")}},
                "interfaces": edges({"name": v("Vlan100")}, {"name": v("Ethernet3.183")}),
            },
        )
    }


def _device():
    return {"name": "PE-A", "vrf_instances": parse_vrf_instances(_device_node())}


def test_parse_instances():
    assert _device()["vrf_instances"] == {
        "s100": {"rd": "10.255.0.1:100", "interfaces": {"Vlan100", "Ethernet3.183"}},
    }


def test_rd_is_per_device():
    assert vrf_rd(_device(), "s100") == "10.255.0.1:100"


def test_missing_instance_fails_the_artifact():
    with pytest.raises(ValueError, match="no IpamVRFRouteDistinguisher"):
        vrf_rd(_device(), "CUSTC-PROD")


def test_interface_vrf_explicit_binding_wins():
    dev = _device()
    svi = {"name": "Vlan100", "role": None, "remote": None}
    assert interface_vrf(dev, svi, {}) == "s100"


def test_contract_fallback_still_applies():
    dev = _device()
    cust = {"name": "Ethernet2", "role": "cust", "remote": {"device": "ce-1"}}
    assert interface_vrf(dev, cust, {"ce-1": "CUSTC-PROD"}) == "CUSTC-PROD"
    core = {"name": "Ethernet1", "role": "core", "remote": {"device": "ce-1"}}
    assert interface_vrf(dev, core, {"ce-1": "CUSTC-PROD"}) is None


# --- check --------------------------------------------------------------

def _ci(vrf, dev, rd=None, mode="auto", members=()):
    """IpamVRFRouteDistinguisher as the check's query returns it; members =
    (iface id, owning device, name, l2_mode)."""
    return {
        "id": f"{vrf}@{dev}",
        "route_distinguisher": v(rd),
        "rd_mode": v(mode),
        "vrf": {"node": {"name": v(vrf)}},
        "device": {"node": {"name": v(dev)}},
        "interfaces": edges(*[
            {"id": i, "name": v(n), "device": {"node": {"name": v(o)}}, "l2_mode": v(m)}
            for i, o, n, m in members
        ]),
    }


def _msgs(instances):
    return [m for m, _ in find_violations(instances)]


def test_check_clean():
    assert _msgs([
        _ci("s100", "PE-A", "10.0.0.1:100", members=[("a1", "PE-A", "Vlan100", None)]),
        _ci("s100", "PE-B", "10.0.0.2:100", members=[("b1", "PE-B", "Vlan100", None)]),
        _ci("v183", "PE-A", "10.0.0.1:183", members=[("a2", "PE-A", "Et3.183", None)]),
        _ci("v211", "PE-C"),  # auto, generator not run yet: not an error
    ]) == []


def test_check_duplicate_rd_across_vrfs_and_pes():
    (msg,) = _msgs([_ci("s100", "PE-A", "10.0.0.1:100"), _ci("v183", "PE-B", "10.0.0.1:100")])
    assert "RD 10.0.0.1:100 is used by 2 instances: s100@PE-A, v183@PE-B" == msg


def test_check_manual_without_rd():
    (msg,) = _msgs([_ci("s100", "PE-A", mode="manual")])
    assert "manual but no route_distinguisher" in msg


def test_check_rejects_switchport_member():
    (msg,) = _msgs([_ci("s100", "PE-A", "10.0.0.1:100", members=[("a1", "PE-A", "Ethernet3", "trunk")])])
    assert "PE-A:Ethernet3 is a switchport (l2_mode=trunk)" in msg


def test_check_rejects_foreign_device_member():
    (msg,) = _msgs([_ci("s100", "PE-A", "10.0.0.1:100", members=[("b1", "PE-B", "Vlan100", None)])])
    assert "belongs to PE-B, not to the instance's device PE-A" in msg


def test_check_rejects_interface_in_two_vrfs():
    (msg,) = _msgs([
        _ci("s100", "PE-A", "10.0.0.1:100", members=[("a1", "PE-A", "Vlan100", None)]),
        _ci("v183", "PE-A", "10.0.0.1:183", members=[("a1", "PE-A", "Vlan100", None)]),
    ])
    assert "PE-A:Vlan100 is a member of 2 VRF instances (s100@PE-A, v183@PE-A)" in msg
