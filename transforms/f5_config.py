"""F5 BIG-IP LTM desired state, rendered as YAML for terraform/f5_ltm.

One DcimLoadBalancer in, one YAML document out. The document holds the LTM
objects Infrahub owns on that box (health monitors, nodes, pools with their
members, SNAT pools and virtual servers) and is read by the Terraform module
in terraform/f5_ltm, which pushes it with the F5Networks/bigip provider.

The device reaches its LTM objects only through its cluster
(``DcimLoadBalancer.cluster``): pools, VIPs and SNAT pools all belong to the
cluster, not to the box. A device without a cluster has nothing to render,
and the artifact fails rather than producing an empty config.

Contract with the Terraform module (bump ``FORMAT_VERSION`` on any change):
  * every object collection is a map keyed by the object's full path
    (``/Partition/name``). The key is the Terraform ``for_each`` key and the
    F5 name, so it must be stable: renaming an object replaces it.
  * field names are the bigip provider's argument names and values are already
    in provider vocabulary (``enabled``/``disabled``, full paths, F5 profile
    names). Every vendor decision is made here; the module only maps fields.
  * built-in objects (``/Common/tcp``, ``/Common/fastL4``, the default monitor
    parents) are referenced, never defined.

Terraform manages exactly what is listed. Unlike the old tmsh merge file, an
object removed from Infrahub is DELETED from the box on the next apply, and
objects Infrahub never listed are left alone.
"""

import ipaddress

import yaml
from infrahub_sdk.transforms import InfrahubTransform

FORMAT_VERSION = 1
DEFAULT_PARTITION = "Common"

# Infrahub dropdown value -> F5 built-in profile / keyword. Vendor vocabulary
# lives here, never in the schema.
PERSISTENCE_PROFILES = {
    "source_ip": "source_addr",
    "cookie": "cookie",
    "ssl_session": "ssl",
    "universal": "universal",
}
MONITOR_TYPES = {
    "tcp": "tcp",
    "udp": "udp",
    "http": "http",
    "https": "https",
    "icmp": "gateway_icmp",
}
# Profiles a virtual gets when Infrahub names none, by virtual_server_type.
TYPE_DEFAULT_PROFILES = {
    "performance_l4": ["fastL4"],
    "performance_http": ["fasthttp"],
}
# ip-protocol per VIP protocol; http/https are L7 over TCP.
IP_PROTOCOLS = {"tcp": "tcp", "udp": "udp", "http": "tcp", "https": "tcp", "any": "any"}
SNAT_TYPES = {"snat_pool": "snat", "automap": "automap", "none": "none"}


def _v(attr):
    return (attr or {}).get("value")


def _node(rel):
    return (rel or {}).get("node")


def _edges(rel):
    return [e["node"] for e in (rel or {}).get("edges", [])]


def _default(value, fallback):
    return fallback if value is None else value


def full_path(name, partition=None):
    """`/Partition/name`; a value that is already a path is kept as-is."""
    if name.startswith("/"):
        return name
    return f"/{partition or DEFAULT_PARTITION}/{name}"


def host(cidr):
    return str(ipaddress.ip_interface(cidr).ip)


def enabled(flag):
    return "enabled" if flag else "disabled"


def monitor_view(hc):
    kind = _v(hc["type_checks"])
    if kind not in MONITOR_TYPES:
        raise ValueError(
            f"health check {_v(hc['name'])}: no F5 monitor type for '{kind}'"
        )
    view = {"parent": f"/Common/{MONITOR_TYPES[kind]}"}
    for key, field in (
        ("interval", "interval"),
        ("timeout", "timeout"),
        ("send", "send_string"),
        ("receive", "receive_string"),
    ):
        if _v(hc[field]) is not None:
            view[key] = _v(hc[field])
    return full_path(_v(hc["name"])), view


def pool_view(pool, nodes, monitors):
    partition = _v(pool["partition"]) or DEFAULT_PARTITION
    hc = _node(pool.get("health_check"))
    pool_monitors = []
    if hc:
        path, mon = monitor_view(hc)
        monitors[path] = mon
        pool_monitors.append(path)

    members = {}
    for m in _edges(pool["members"]):
        ip_rel = _node(m.get("ip_address"))
        if not ip_rel:
            raise ValueError(
                f"pool {_v(pool['name'])}: member {_v(m['name'])} has no ip_address"
            )
        address = host(_v(ip_rel["address"]))
        port = _default(_v(m["port"]), 0)
        node_path = full_path(address, partition)
        # Node monitor "default" = inherit the box's default node monitor (the F5
        # default; the bigip provider would otherwise set /Common/icmp).
        nodes.setdefault(node_path, {"address": address, "monitor": "default"})
        sep = "." if ":" in address else ":"  # IPv6 members are addr.port
        members[f"{node_path}{sep}{port}"] = {
            "ratio": _default(_v(m["ratio"]), 1),
            "priority_group": _default(_v(m["priority"]), 0),
            "connection_limit": _default(_v(m["connection_limit"]), 0),
            "state": enabled(_v(m["admin_enabled"]) is not False),
        }

    view = {
        "description": _v(pool["description"]),
        "load_balancing_mode": _v(pool["load_balancing_method"]) or "round-robin",
        "minimum_active_members": _default(_v(pool["min_active_members"]), 0),
        "slow_ramp_time": _default(_v(pool["slow_ramp_time"]), 10),
        "service_down_action": _v(pool.get("service_down_action")) or "none",
        "reselect_tries": _default(_v(pool.get("reselect_tries")), 0),
        "allow_snat": "yes" if _v(pool.get("allow_snat")) is not False else "no",
        "allow_nat": "yes" if _v(pool.get("allow_nat")) is not False else "no",
        "monitors": pool_monitors,
        "members": dict(sorted(members.items())),
    }
    return full_path(_v(pool["name"]), partition), view


def snatpool_view(sp):
    partition = _v(sp["partition"]) or DEFAULT_PARTITION
    addresses = sorted(host(_v(a["address"])) for a in _edges(sp["addresses"]))
    if not addresses:
        raise ValueError(f"SNAT pool {_v(sp['name'])} has no addresses")
    return full_path(_v(sp["name"]), partition), {
        "members": [full_path(a, partition) for a in addresses],
    }


def vip_profiles(vip):
    """Profiles split by context, the way the bigip provider takes them."""
    explicit = [full_path(p) for p in (_v(vip.get("attached_profiles")) or [])]
    client = [full_path(_v(vip[f])) for f in ("client_ssl_profile",) if _v(vip.get(f))]
    server = [full_path(_v(vip[f])) for f in ("server_ssl_profile",) if _v(vip.get(f))]
    both = explicit + [
        full_path(_v(vip[f]))
        for f in ("client_http_profile", "server_http_profile", "one_connect_profile")
        if _v(vip.get(f))
    ]
    if not (both or client or server):
        defaults = TYPE_DEFAULT_PROFILES.get(_v(vip.get("virtual_server_type")))
        if defaults is None:
            protocol = _v(vip["protocol"])
            defaults = (
                ["udp"]
                if protocol == "udp"
                else ["tcp"] + (["http"] if protocol in ("http", "https") else [])
            )
        both = [full_path(p) for p in defaults]
    return sorted(set(both)), sorted(set(client)), sorted(set(server))


def vip_view(vip):
    name = _v(vip["name"])
    partition = _v(vip["partition"]) or DEFAULT_PARTITION
    ip_rel = _node(vip.get("vip_ip"))
    if not ip_rel:
        raise ValueError(f"VIP {name} has no vip_ip")
    address = ipaddress.ip_interface(_v(ip_rel["address"]))
    route_domain = _v(vip["route_domain"])

    pool = _node(vip.get("pool"))
    snat_type = _v(vip.get("snat_type")) or "none"
    snat_pool = _node(vip.get("snat_pool"))
    if snat_type == "snat_pool" and not snat_pool:
        raise ValueError(f"VIP {name}: snat_type is snat_pool but no snat_pool is set")

    profiles, client_profiles, server_profiles = vip_profiles(vip)
    persistence = PERSISTENCE_PROFILES.get(_v(vip.get("persistence_type")))
    return full_path(name, partition), {
        "description": _v(vip["description"]),
        "destination": f"{address.ip}%{route_domain}"
        if route_domain
        else str(address.ip),
        "port": _default(_v(vip["port"]), 0),
        "mask": str(address.network.netmask),
        "ip_protocol": IP_PROTOCOLS.get(_v(vip["protocol"]), "tcp"),
        "source": _v(vip["source_address_filter"]) or "0.0.0.0/0",
        "pool": full_path(_v(pool["name"]), _v(pool["partition"])) if pool else None,
        "profiles": profiles,
        "client_profiles": client_profiles,
        "server_profiles": server_profiles,
        "persistence_profiles": [f"/Common/{persistence}"] if persistence else [],
        "irules": [full_path(r) for r in (_v(vip.get("irules")) or [])],
        "source_address_translation": SNAT_TYPES[snat_type],
        "snatpool": full_path(_v(snat_pool["name"]), _v(snat_pool["partition"]))
        if snat_type == "snat_pool"
        else None,
        "translate_address": enabled(_default(_v(vip.get("translate_address")), True)),
        "translate_port": enabled(_default(_v(vip.get("translate_port")), True)),
        "vlans": sorted(full_path(v) for v in (_v(vip.get("vlans")) or [])),
        "vlans_enabled": _v(vip.get("vlans_enabled")) is not False
        and bool(_v(vip.get("vlans"))),
        "connection_limit": _default(_v(vip.get("connection_limit")), 0),
        "source_port": _v(vip.get("source_port")) or "preserve",
        "state": enabled(_v(vip["admin_enabled"]) is not False),
    }


def build_view(data):
    edges = data["DcimLoadBalancer"]["edges"]
    if not edges:
        raise ValueError("load balancer not found")
    device = edges[0]["node"]
    cluster = _node(device.get("cluster"))
    if not cluster:
        raise ValueError(
            f"{_v(device['name'])} has no cluster: pools and VIPs attach to a load balancer only through its cluster"
        )
    mgmt = _node(device.get("primary_address"))
    nodes, monitors = {}, {}
    pools = dict(pool_view(p, nodes, monitors) for p in _edges(cluster["pools"]))
    snatpools = dict(snatpool_view(s) for s in _edges(cluster["snat_pools"]))
    virtuals = dict(vip_view(v) for v in _edges(cluster["vips"]))

    for path, vs in virtuals.items():
        if vs["snatpool"] and vs["snatpool"] not in snatpools:
            raise ValueError(
                f"VIP {path}: SNAT pool {vs['snatpool']} is not on this cluster"
            )
        if vs["pool"] and vs["pool"] not in pools:
            raise ValueError(f"VIP {path}: pool {vs['pool']} is not on this cluster")

    return {
        "format_version": FORMAT_VERSION,
        "device": {
            "name": _v(device["name"]),
            "cluster": _v(cluster["name"]),
            "management_address": host(_v(mgmt["address"])) if mgmt else None,
            "os_version": _v(device["os_version"]),
        },
        "monitors": dict(sorted(monitors.items())),
        "nodes": dict(sorted(nodes.items())),
        "pools": dict(sorted(pools.items())),
        "snatpools": dict(sorted(snatpools.items())),
        "virtual_servers": dict(sorted(virtuals.items())),
    }


def render(view):
    header = (
        f"# F5 LTM desired state for {view['device']['name']} "
        f"(cluster {view['device']['cluster']}), rendered from Infrahub.\n"
        "# Applied by terraform/f5_ltm; do not edit on the box.\n"
    )
    return header + yaml.safe_dump(
        view, sort_keys=False, default_flow_style=False, width=1000
    )


class F5ConfigTransform(InfrahubTransform):
    query = "f5_config"

    async def transform(self, data):
        return render(build_view(data))
