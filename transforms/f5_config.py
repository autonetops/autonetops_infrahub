"""F5 BIG-IP LTM configuration renderer.

One DcimLoadBalancer in, its LTM configuration out, in bigip.conf (tmsh)
syntax: health monitors, nodes, pools, SNAT pools and virtual servers.

The device reaches its LTM objects only through its cluster
(``DcimLoadBalancer.cluster``): pools, VIPs and SNAT pools all belong to the
cluster, not to the box. A device without a cluster has nothing to render,
and the artifact fails rather than producing an empty config.

The output is a *merge* file. ``tmsh load sys config merge file <f>`` creates
or updates the objects below but deletes nothing, so objects present on the
box and missing from Infrahub stay in place. Built-in objects (``/Common/tcp``,
``/Common/fastL4``, the default monitors) are referenced, never defined.

Hybrid rendering, as in device_config.py: Python builds a flat view and owns
every decision (names, defaults, profile selection); templates/f5_bigip_ltm.j2
owns the tmsh syntax. StrictUndefined turns a view/template mismatch into a
failed artifact instead of a silently blank line.
"""

import ipaddress
import pathlib

import jinja2
from infrahub_sdk.transforms import InfrahubTransform

TEMPLATE_DIR = pathlib.Path(__file__).resolve().parent.parent / "templates"
_JINJA = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)),
    undefined=jinja2.StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)

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


def _v(attr):
    return (attr or {}).get("value")


def _node(rel):
    return (rel or {}).get("node")


def _edges(rel):
    return [e["node"] for e in (rel or {}).get("edges", [])]


def full_path(name, partition=None):
    """`/Partition/name`; a value that is already a path is kept as-is."""
    if name.startswith("/"):
        return name
    return f"/{partition or DEFAULT_PARTITION}/{name}"


def host(cidr):
    return str(ipaddress.ip_interface(cidr).ip)


def destination(address, port, route_domain, partition):
    """F5 destination: `/P/10.0.0.1:80`, `/P/10.0.0.1%2:80`, IPv6 `/P/2001:db8::1.80`."""
    ip = ipaddress.ip_interface(address)
    rd = f"%{route_domain}" if route_domain else ""
    sep = "." if ip.version == 6 else ":"
    return f"/{partition}/{ip.ip}{rd}{sep}{port}"


def mask(address):
    return str(ipaddress.ip_interface(address).network.netmask)


def enabled(flag):
    return "enabled" if flag else "disabled"


def monitor_view(hc):
    kind = _v(hc["type_checks"])
    if kind not in MONITOR_TYPES:
        raise ValueError(
            f"health check {_v(hc['name'])}: no F5 monitor type for '{kind}'"
        )
    settings = [
        (key, value)
        for key, value in (
            ("interval", _v(hc["interval"])),
            ("timeout", _v(hc["timeout"])),
            ("send", _v(hc["send_string"])),
            ("recv", _v(hc["receive_string"])),
        )
        if value is not None
    ]
    return {
        "path": full_path(_v(hc["name"])),
        "type": MONITOR_TYPES[kind],
        "defaults_from": f"/Common/{MONITOR_TYPES[kind]}",
        "settings": settings,
    }


def pool_view(pool, nodes, monitors):
    partition = _v(pool["partition"]) or DEFAULT_PARTITION
    hc = _node(pool.get("health_check"))
    monitor = None
    if hc:
        mon = monitor_view(hc)
        monitors[mon["path"]] = mon
        monitor = mon["path"]

    members = []
    for m in _edges(pool["members"]):
        ip_rel = _node(m.get("ip_address"))
        if not ip_rel:
            raise ValueError(
                f"pool {_v(pool['name'])}: member {_v(m['name'])} has no ip_address"
            )
        address = host(_v(ip_rel["address"]))
        port = _v(m["port"]) if _v(m["port"]) is not None else 0
        node_path = full_path(address, partition)
        nodes.setdefault(node_path, {"path": node_path, "address": address})
        settings = [
            (key, value)
            for key, value in (
                ("connection-limit", _v(m["connection_limit"])),
                ("priority-group", _v(m["priority"])),
                ("ratio", _v(m["ratio"])),
            )
            if value not in (None, 0) and not (key == "ratio" and value == 1)
        ]
        if _v(m["admin_enabled"]) is False:
            settings.append(("session", "user-disabled"))
        if _v(m["description"]):
            settings.append(("description", quote(_v(m["description"]))))
        members.append(
            {
                "path": f"{node_path}:{port}"
                if ":" not in address
                else f"{node_path}.{port}",
                "address": address,
                "settings": sorted(settings),
            }
        )

    settings = [
        ("load-balancing-mode", _v(pool["load_balancing_method"]) or "round-robin")
    ]
    for key, value in (
        ("min-active-members", _v(pool["min_active_members"])),
        ("slow-ramp-time", _v(pool["slow_ramp_time"])),
    ):
        if value is not None:
            settings.append((key, value))
    if monitor:
        settings.append(("monitor", monitor))
    if _v(pool["description"]):
        settings.append(("description", quote(_v(pool["description"]))))
    return {
        "path": full_path(_v(pool["name"]), partition),
        "members": sorted(members, key=lambda m: m["path"]),
        "settings": sorted(settings),
    }


def snatpool_view(sp):
    partition = _v(sp["partition"]) or DEFAULT_PARTITION
    addresses = sorted(host(_v(a["address"])) for a in _edges(sp["addresses"]))
    if not addresses:
        raise ValueError(f"SNAT pool {_v(sp['name'])} has no addresses")
    return {
        "path": full_path(_v(sp["name"]), partition),
        "members": [full_path(a, partition) for a in addresses],
        "description": quote(_v(sp["description"])) if _v(sp["description"]) else None,
    }


def vip_profiles(vip):
    """(path, context) pairs: explicit list + typed fields, else the type default."""
    profiles = {full_path(p): None for p in (_v(vip.get("attached_profiles")) or [])}
    for field, context in (
        ("client_ssl_profile", "clientside"),
        ("server_ssl_profile", "serverside"),
        ("client_http_profile", None),
        ("server_http_profile", None),
        ("one_connect_profile", None),
    ):
        if _v(vip.get(field)):
            profiles[full_path(_v(vip[field]))] = context
    if not profiles:
        defaults = TYPE_DEFAULT_PROFILES.get(_v(vip.get("virtual_server_type")))
        if defaults is None:
            protocol = _v(vip["protocol"])
            defaults = (
                ["udp"]
                if protocol == "udp"
                else ["tcp"] + (["http"] if protocol in ("http", "https") else [])
            )
        profiles = {full_path(p): None for p in defaults}
    return sorted(profiles.items())


def vip_view(vip):
    name = _v(vip["name"])
    partition = _v(vip["partition"]) or DEFAULT_PARTITION
    ip_rel = _node(vip.get("vip_ip"))
    if not ip_rel:
        raise ValueError(f"VIP {name} has no vip_ip")
    address = _v(ip_rel["address"])
    protocol = _v(vip["protocol"])

    pool = _node(vip.get("pool"))
    snat_type = _v(vip.get("snat_type")) or "none"
    snat_pool = _node(vip.get("snat_pool"))
    if snat_type == "snat_pool" and not snat_pool:
        raise ValueError(f"VIP {name}: snat_type is snat_pool but no snat_pool is set")
    translation = {
        "snat_pool": {
            "type": "snat",
            "pool": full_path(_v(snat_pool["name"]), _v(snat_pool["partition"]))
            if snat_pool
            else None,
        },
        "automap": {"type": "automap", "pool": None},
        "none": {"type": "none", "pool": None},
    }[snat_type]

    persistence = PERSISTENCE_PROFILES.get(_v(vip.get("persistence_type")))
    vlans = sorted(full_path(v) for v in (_v(vip.get("vlans")) or []))
    translate_address = _v(vip.get("translate_address"))
    translate_port = _v(vip.get("translate_port"))
    return {
        "path": full_path(name, partition),
        "description": quote(_v(vip["description"]))
        if _v(vip["description"])
        else None,
        "destination": destination(
            address, _v(vip["port"]), _v(vip["route_domain"]), partition
        ),
        "ip_protocol": IP_PROTOCOLS.get(protocol, "tcp"),
        "mask": mask(address),
        "pool": full_path(_v(pool["name"]), _v(pool["partition"])) if pool else None,
        "profiles": vip_profiles(vip),
        "persist": f"/Common/{persistence}" if persistence else None,
        "rules": [full_path(r) for r in (_v(vip.get("irules")) or [])],
        "source": _v(vip["source_address_filter"]) or "0.0.0.0/0",
        "translation": translation,
        "translate_address": enabled(
            True if translate_address is None else translate_address
        ),
        "translate_port": enabled(True if translate_port is None else translate_port),
        "vlans": vlans,
        "vlans_mode": "vlans-enabled"
        if _v(vip.get("vlans_enabled")) is not False
        else "vlans-disabled",
        "disabled": _v(vip["admin_enabled"]) is False,
    }


def quote(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


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
    nodes, monitors = {}, {}
    pools = [pool_view(p, nodes, monitors) for p in _edges(cluster["pools"])]
    return {
        "device": _v(device["name"]),
        "cluster": _v(cluster["name"]),
        "os_version": _v(device["os_version"]),
        "monitors": sorted(monitors.values(), key=lambda m: m["path"]),
        "nodes": sorted(nodes.values(), key=lambda n: n["path"]),
        "pools": sorted(pools, key=lambda p: p["path"]),
        "snatpools": sorted(
            (snatpool_view(s) for s in _edges(cluster["snat_pools"])),
            key=lambda s: s["path"],
        ),
        "virtuals": sorted(
            (vip_view(v) for v in _edges(cluster["vips"])), key=lambda v: v["path"]
        ),
    }


class F5ConfigTransform(InfrahubTransform):
    query = "f5_config"

    async def transform(self, data):
        return _JINJA.get_template("f5_bigip_ltm.j2").render(**build_view(data))
