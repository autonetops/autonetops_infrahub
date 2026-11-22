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
from generators.vrf_rd import VrfRdGenerator, derive_rd, loopback_ipv4  # noqa: E402
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


def _vrf(vrf_id, *instances):
    return {"IpamVRF": edges({"name": v("s100"), "vrf_id": v(vrf_id), "instances": edges(*instances)})}


def _inst(dev, lo, rd=None, mode="auto"):
    return {
        "id": dev,
        "route_distinguisher": v(rd),
        "rd_mode": v(mode),
        "device": {"node": {"name": v(dev), "interfaces": edges(iface("Loopback0", lo))}},
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
    with pytest.raises(ValueError, match="no IpamVRFInstance"):
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
    """IpamVRFInstance as the check's query returns it; members =
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
