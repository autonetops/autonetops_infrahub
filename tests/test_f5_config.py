"""Offline tests for transforms/f5_config.py (no Infrahub needed).

LB-DC2 exercises only part of the renderer (fastL4 port-0 virtuals, SNAT
pools). These fixtures cover the rest: monitors, persistence, SSL/HTTP
profiles, IPv6 and route-domain destinations, disabled objects and the
failure modes that must fail the artifact instead of rendering nonsense.
"""

import asyncio
import copy
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from transforms.f5_config import F5ConfigTransform, build_view  # noqa: E402


def v(value):
    return {"value": value}


def rel(node):
    return {"node": node}


def edges(*nodes):
    return {"edges": [{"node": n} for n in nodes]}


def member(address, port, **kw):
    return {
        "name": v(f"{address}:{port}"),
        "description": v(kw.get("description")),
        "port": v(port),
        "ratio": v(kw.get("ratio", 1)),
        "priority": v(kw.get("priority", 0)),
        "connection_limit": v(kw.get("connection_limit", 0)),
        "admin_enabled": v(kw.get("admin_enabled", True)),
        "ip_address": rel({"address": v(f"{address}/32")}),
    }


def vip(name, address, port, **kw):
    return {
        "name": v(name),
        "partition": v(kw.get("partition", "Common")),
        "description": v(kw.get("description")),
        "port": v(port),
        "protocol": v(kw.get("protocol", "tcp")),
        "route_domain": v(kw.get("route_domain")),
        "source_address_filter": v(kw.get("source")),
        "admin_enabled": v(kw.get("admin_enabled", True)),
        "vip_ip": rel({"address": v(address)}),
        "pool": rel({"name": v("web"), "partition": v("Common")}),
        "virtual_server_type": v(kw.get("virtual_server_type", "standard")),
        "attached_profiles": v(kw.get("attached_profiles")),
        "client_ssl_profile": v(kw.get("client_ssl_profile")),
        "server_ssl_profile": v(kw.get("server_ssl_profile")),
        "client_http_profile": v(kw.get("client_http_profile")),
        "server_http_profile": v(None),
        "one_connect_profile": v(None),
        "persistence_type": v(kw.get("persistence_type")),
        "irules": v(kw.get("irules")),
        "vlans": v(kw.get("vlans")),
        "vlans_enabled": v(kw.get("vlans_enabled", True)),
        "translate_address": v(kw.get("translate_address")),
        "translate_port": v(kw.get("translate_port")),
        "snat_type": v(kw.get("snat_type", "automap")),
        "snat_pool": rel(kw.get("snat_pool")),
    }


def data(vips, pools=None, snat_pools=(), cluster=True):
    pools = (
        pools
        if pools is not None
        else [
            {
                "name": v("web"),
                "partition": v("Common"),
                "description": v(None),
                "load_balancing_method": v("least-connections-member"),
                "slow_ramp_time": v(None),
                "min_active_members": v(None),
                "health_check": rel(
                    {
                        "name": v("hc_http"),
                        "type_checks": v("http"),
                        "interval": v(5),
                        "timeout": v(16),
                        "send_string": v(
                            '"GET /health HTTP/1.1\\r\\nHost: x\\r\\n\\r\\n"'
                        ),
                        "receive_string": v('"200 OK"'),
                    }
                ),
                "members": edges(
                    member("10.1.1.10", 80),
                    member("10.1.1.11", 80, admin_enabled=False, ratio=3),
                ),
            }
        ]
    )
    return {
        "DcimLoadBalancer": edges(
            {
                "name": v("LB-TEST"),
                "os_version": v("17.5.0"),
                "platform": rel({"name": v("f5")}),
                "primary_address": rel({"address": v("192.0.2.1/24")}),
                "cluster": rel(
                    {
                        "name": v("LB-TEST"),
                        "pools": edges(*pools),
                        "vips": edges(*vips),
                        "snat_pools": edges(*snat_pools),
                    }
                )
                if cluster
                else rel(None),
            }
        )
    }


def render(d):
    return asyncio.run(F5ConfigTransform.transform(None, d))


def test_https_vip_profiles_persistence_monitor():
    out = render(
        data(
            [
                vip(
                    "vs_https",
                    "10.20.0.80/32",
                    443,
                    protocol="https",
                    client_ssl_profile="clientssl",
                    client_http_profile="http",
                    persistence_type="cookie",
                    irules=["redirect"],
                    vlans=["/Common/ext"],
                )
            ]
        )
    )
    assert (
        "ltm monitor http /Common/hc_http {\n    defaults-from /Common/http\n    interval 5\n"
        in out
    )
    assert "    monitor /Common/hc_http\n" in out
    assert "    load-balancing-mode least-connections-member\n" in out
    assert (
        "        /Common/clientssl {\n            context clientside\n        }\n"
        in out
    )
    assert "        /Common/http {\n        }\n" in out
    assert "/Common/tcp" not in out  # explicit profiles replace the type default
    assert "    persist {\n        /Common/cookie {\n            default yes\n" in out
    assert "    rules {\n        /Common/redirect\n    }\n" in out
    assert "    ip-protocol tcp\n" in out
    assert "        type automap\n" in out
    assert "    translate-address enabled\n    translate-port enabled\n" in out
    assert "    vlans {\n        /Common/ext\n    }\n    vlans-enabled\n" in out


def test_member_settings_and_nodes():
    out = render(data([vip("vs", "10.20.0.80/32", 80)]))
    assert out.count("ltm node ") == 2
    assert (
        "        /Common/10.1.1.11:80 {\n            address 10.1.1.11\n            ratio 3\n            session user-disabled\n"
        in out
    )
    assert (
        "/Common/10.1.1.10:80 {\n            address 10.1.1.10\n        }\n" in out
    )  # defaults omitted


def test_default_profiles_by_type_and_protocol():
    view = build_view(
        data(
            [
                vip(
                    "l4",
                    "10.20.0.1/32",
                    0,
                    protocol="any",
                    virtual_server_type="performance_l4",
                ),
                vip("udp", "10.20.0.2/32", 53, protocol="udp"),
                vip("http", "10.20.0.3/32", 80, protocol="http"),
            ]
        )
    )
    profiles = {x["path"]: [p for p, _ in x["profiles"]] for x in view["virtuals"]}
    assert profiles == {
        "/Common/l4": ["/Common/fastL4"],
        "/Common/udp": ["/Common/udp"],
        "/Common/http": ["/Common/http", "/Common/tcp"],
    }


def test_ipv6_route_domain_and_disabled():
    view = build_view(
        data(
            [
                vip("v6", "2001:db8::80/128", 443, partition="Tenant1"),
                vip(
                    "rd",
                    "10.20.0.5/32",
                    80,
                    route_domain=2,
                    admin_enabled=False,
                    vlans=["int"],
                    vlans_enabled=False,
                ),
            ]
        )
    )
    by = {x["path"]: x for x in view["virtuals"]}
    assert by["/Tenant1/v6"]["destination"] == "/Tenant1/2001:db8::80.443"
    assert by["/Tenant1/v6"]["mask"] == "ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff"
    assert by["/Common/rd"]["destination"] == "/Common/10.20.0.5%2:80"
    assert by["/Common/rd"]["disabled"] is True
    assert by["/Common/rd"]["vlans_mode"] == "vlans-disabled"


def test_snat_pool_reference():
    sp = {
        "name": v("snat_out"),
        "partition": v("Common"),
        "description": v(None),
        "addresses": edges(
            {"address": v("198.51.100.9/32")}, {"address": v("198.51.100.8/32")}
        ),
    }
    out = render(
        data(
            [
                vip(
                    "vs",
                    "10.20.0.80/32",
                    80,
                    snat_type="snat_pool",
                    snat_pool={"name": v("snat_out"), "partition": v("Common")},
                )
            ],
            snat_pools=[sp],
        )
    )
    assert (
        "ltm snatpool /Common/snat_out {\n    members {\n        /Common/198.51.100.8\n        /Common/198.51.100.9\n"
        in out
    )
    assert "        pool /Common/snat_out\n        type snat\n" in out


def test_output_is_deterministic():
    d = data([vip("b", "10.20.0.2/32", 80), vip("a", "10.20.0.1/32", 80)])
    shuffled = copy.deepcopy(d)
    shuffled["DcimLoadBalancer"]["edges"][0]["node"]["cluster"]["node"]["vips"][
        "edges"
    ].reverse()
    assert render(d) == render(shuffled)
    assert render(d).index("ltm virtual /Common/a") < render(d).index(
        "ltm virtual /Common/b"
    )


@pytest.mark.parametrize(
    "case, message",
    [
        ("no_cluster", "has no cluster"),
        ("snat_without_pool", "snat_type is snat_pool but no snat_pool"),
        ("vip_without_ip", "has no vip_ip"),
        ("unknown", "load balancer not found"),
    ],
)
def test_failures_fail_the_artifact(case, message):
    if case == "no_cluster":
        d = data([], cluster=False)
    elif case == "snat_without_pool":
        d = data([vip("vs", "10.20.0.80/32", 80, snat_type="snat_pool")])
    elif case == "vip_without_ip":
        bad = vip("vs", "10.20.0.80/32", 80)
        bad["vip_ip"] = rel(None)
        d = data([bad])
    else:
        d = {"DcimLoadBalancer": {"edges": []}}
    with pytest.raises(ValueError, match=message):
        build_view(d)
