"""Offline tests for transforms/f5_config.py (no Infrahub needed).

The transform renders the YAML that terraform/f5_ltm reads. LB-DC2 exercises
only part of it (fastL4 port-0 virtuals, SNAT pools). These fixtures cover the
rest: monitors, persistence, SSL/HTTP profiles, IPv6 and route-domain
destinations, disabled objects and the failure modes that must fail the
artifact instead of rendering nonsense.
"""

import asyncio
import copy
import pathlib
import sys

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from transforms.f5_config import FORMAT_VERSION, F5ConfigTransform, build_view  # noqa: E402


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
        "connection_limit": v(kw.get("connection_limit")),
        "source_port": v(kw.get("source_port")),
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
                "service_down_action": v("reset"),
                "reselect_tries": v(None),
                "allow_snat": v(True),
                "allow_nat": v(False),
                "health_check": rel(
                    {
                        "name": v("hc_http"),
                        "type_checks": v("http"),
                        "interval": v(5),
                        "timeout": v(16),
                        "send_string": v(
                            "GET /health HTTP/1.1\\r\\nHost: x\\r\\n\\r\\n"
                        ),
                        "receive_string": v("200 OK"),
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


def virtuals(d):
    return build_view(d)["virtual_servers"]


def test_output_is_yaml_terraform_can_read():
    doc = yaml.safe_load(render(data([vip("vs", "10.20.0.80/32", 80)])))
    assert doc["format_version"] == FORMAT_VERSION
    assert doc["device"] == {
        "name": "LB-TEST",
        "cluster": "LB-TEST",
        "management_address": "192.0.2.1",
        "os_version": "17.5.0",
    }
    assert list(doc) == [
        "format_version",
        "device",
        "monitors",
        "nodes",
        "pools",
        "snatpools",
        "virtual_servers",
    ]


def test_https_vip_profiles_persistence_monitor():
    view = build_view(
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
                    connection_limit=500,
                )
            ]
        )
    )
    assert view["monitors"] == {
        "/Common/hc_http": {
            "parent": "/Common/http",
            "interval": 5,
            "timeout": 16,
            "send": "GET /health HTTP/1.1\\r\\nHost: x\\r\\n\\r\\n",
            "receive": "200 OK",
        }
    }
    pool = view["pools"]["/Common/web"]
    assert pool["monitors"] == ["/Common/hc_http"]
    assert pool["load_balancing_mode"] == "least-connections-member"
    assert pool["service_down_action"] == "reset"
    assert (pool["allow_snat"], pool["allow_nat"]) == ("yes", "no")
    vs = view["virtual_servers"]["/Common/vs_https"]
    assert vs["client_profiles"] == ["/Common/clientssl"]
    assert vs["profiles"] == [
        "/Common/http"
    ]  # explicit profiles replace the type default
    assert vs["server_profiles"] == []
    assert vs["persistence_profiles"] == ["/Common/cookie"]
    assert vs["irules"] == ["/Common/redirect"]
    assert vs["ip_protocol"] == "tcp"
    assert (vs["source_address_translation"], vs["snatpool"]) == ("automap", None)
    assert (vs["translate_address"], vs["translate_port"]) == ("enabled", "enabled")
    assert (vs["vlans"], vs["vlans_enabled"]) == (["/Common/ext"], True)
    assert (vs["connection_limit"], vs["source_port"]) == (500, "preserve")
    assert (vs["destination"], vs["port"], vs["mask"]) == (
        "10.20.0.80",
        443,
        "255.255.255.255",
    )


def test_member_settings_and_nodes():
    view = build_view(data([vip("vs", "10.20.0.80/32", 80)]))
    assert view["nodes"] == {
        "/Common/10.1.1.10": {"address": "10.1.1.10", "monitor": "default"},
        "/Common/10.1.1.11": {"address": "10.1.1.11", "monitor": "default"},
    }
    assert view["pools"]["/Common/web"]["members"] == {
        "/Common/10.1.1.10:80": {
            "ratio": 1,
            "priority_group": 0,
            "connection_limit": 0,
            "state": "enabled",
        },
        "/Common/10.1.1.11:80": {
            "ratio": 3,
            "priority_group": 0,
            "connection_limit": 0,
            "state": "disabled",
        },
    }


def test_default_profiles_by_type_and_protocol():
    vs = virtuals(
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
    assert {k: x["profiles"] for k, x in vs.items()} == {
        "/Common/http": ["/Common/http", "/Common/tcp"],
        "/Common/l4": ["/Common/fastL4"],
        "/Common/udp": ["/Common/udp"],
    }
    assert vs["/Common/l4"]["ip_protocol"] == "any"


def test_ipv6_route_domain_and_disabled():
    pools = data([])["DcimLoadBalancer"]["edges"][0]["node"]["cluster"]["node"][
        "pools"
    ]["edges"]
    pools[0]["node"]["partition"] = v("Tenant1")  # pool referenced from /Tenant1 below
    d = data(
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
        ],
        pools=[p["node"] for p in pools]
        + [
            data([])["DcimLoadBalancer"]["edges"][0]["node"]["cluster"]["node"][
                "pools"
            ]["edges"][0]["node"]
        ],
    )
    for x in d["DcimLoadBalancer"]["edges"][0]["node"]["cluster"]["node"]["vips"][
        "edges"
    ]:
        if x["node"]["name"]["value"] == "v6":
            x["node"]["pool"] = rel({"name": v("web"), "partition": v("Tenant1")})
    vs = virtuals(d)
    assert vs["/Tenant1/v6"]["destination"] == "2001:db8::80"
    assert vs["/Tenant1/v6"]["mask"] == "ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff"
    assert vs["/Tenant1/v6"]["pool"] == "/Tenant1/web"
    assert vs["/Common/rd"]["destination"] == "10.20.0.5%2"
    assert vs["/Common/rd"]["state"] == "disabled"
    assert (vs["/Common/rd"]["vlans"], vs["/Common/rd"]["vlans_enabled"]) == (
        ["/Common/int"],
        False,
    )


def test_snat_pool_reference():
    sp = {
        "name": v("snat_out"),
        "partition": v("Common"),
        "description": v(None),
        "addresses": edges(
            {"address": v("198.51.100.9/32")}, {"address": v("198.51.100.8/32")}
        ),
    }
    view = build_view(
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
    assert view["snatpools"] == {
        "/Common/snat_out": {
            "members": ["/Common/198.51.100.8", "/Common/198.51.100.9"]
        }
    }
    vs = view["virtual_servers"]["/Common/vs"]
    assert (vs["source_address_translation"], vs["snatpool"]) == (
        "snat",
        "/Common/snat_out",
    )


def test_output_is_deterministic():
    d = data([vip("b", "10.20.0.2/32", 80), vip("a", "10.20.0.1/32", 80)])
    shuffled = copy.deepcopy(d)
    shuffled["DcimLoadBalancer"]["edges"][0]["node"]["cluster"]["node"]["vips"][
        "edges"
    ].reverse()
    assert render(d) == render(shuffled)
    assert list(virtuals(d)) == ["/Common/a", "/Common/b"]


@pytest.mark.parametrize(
    "case, message",
    [
        ("no_cluster", "has no cluster"),
        ("snat_without_pool", "snat_type is snat_pool but no snat_pool"),
        ("snat_pool_elsewhere", "is not on this cluster"),
        ("vip_without_ip", "has no vip_ip"),
        ("unknown", "load balancer not found"),
    ],
)
def test_failures_fail_the_artifact(case, message):
    if case == "no_cluster":
        d = data([], cluster=False)
    elif case == "snat_without_pool":
        d = data([vip("vs", "10.20.0.80/32", 80, snat_type="snat_pool")])
    elif case == "snat_pool_elsewhere":
        d = data(
            [
                vip(
                    "vs",
                    "10.20.0.80/32",
                    80,
                    snat_type="snat_pool",
                    snat_pool={"name": v("other"), "partition": v("Common")},
                )
            ]
        )
    elif case == "vip_without_ip":
        bad = vip("vs", "10.20.0.80/32", 80)
        bad["vip_ip"] = rel(None)
        d = data([bad])
    else:
        d = {"DcimLoadBalancer": {"edges": []}}
    with pytest.raises(ValueError, match=message):
        build_view(d)


FIXTURES = (
    pathlib.Path(__file__).resolve().parent.parent
    / "terraform/modules/f5_ltm/tests/fixtures"
)


@pytest.mark.parametrize("fixture", ["tftest-v1.yml", "tftest-v2.yml"])
def test_terraform_fixtures_match_the_renderer_contract(fixture):
    """The Terraform module's test fixtures must have the shape this transform renders."""
    rendered = yaml.safe_load(render(data([vip("vs", "10.20.0.80/32", 80)])))
    fx = yaml.safe_load((FIXTURES / fixture).read_text())

    def fields(doc, kind):
        return {k for obj in doc[kind].values() for k in obj}

    assert fx["format_version"] == rendered["format_version"]
    assert list(fx) == list(rendered)
    for kind in ("nodes", "pools", "virtual_servers"):
        assert fields(fx, kind) == fields(rendered, kind), kind
    member_fields = {
        k for p in fx["pools"].values() for m in p["members"].values() for k in m
    }
    assert member_fields == set(
        next(iter(rendered["pools"]["/Common/web"]["members"].values()))
    )
