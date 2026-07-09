"""Intent compiler: device configuration renderer.

One vendor-agnostic intent graph in, one platform-specific configuration
out. The intent model never mentions Junos/XR/EOS knobs - the renderer
selected by ``DcimPlatform.name`` owns every vendor-specific decision.

Intent is stable; renderers change.

Renderer selection is data-driven: adding a vendor means adding one
render function and one platform record in the SoT - the schema, the
checks and every other renderer stay untouched.

Registered renderers: IOS-XE, EOS, Junos, SR Linux, FRR. Which *shape* a
renderer emits (provider edge vs customer edge) also follows from the
contract graph rather than from a flag on the device.
"""

import ipaddress

from infrahub_sdk.transforms import InfrahubTransform

LOOPBACK_PREFIXES = ("loopback", "system", "lo")


# --------------------------------------------------------------------------
# Intent-graph extraction helpers
# --------------------------------------------------------------------------

def _v(attr):
    return attr.get("value") if attr else None


def _node(rel):
    return rel.get("node") if rel else None


def _edges(rel):
    return [e["node"] for e in rel["edges"]] if rel else []


def _iface_ips(iface):
    ips = iface.get("ip_addresses")
    return [_v(ip["address"]) for ip in _edges(ips)] if ips else []


def parse_device(data):
    node = data["DcimDevice"]["edges"][0]["node"]
    platform = _node(node.get("platform")) or {}
    interfaces = []
    for iface in _edges(node["interfaces"]):
        remote = None
        connector = _node(iface.get("connector"))
        if connector:
            for endpoint in _edges(connector["connected_endpoints"]):
                remote_dev = _node(endpoint.get("device"))
                if remote_dev and _v(remote_dev["name"]) != _v(node["name"]):
                    remote = {
                        "device": _v(remote_dev["name"]),
                        "interface": _v(endpoint["name"]),
                        "ips": _iface_ips(endpoint),
                    }
        interfaces.append({
            "name": _v(iface["name"]),
            "description": _v(iface.get("description")),
            "role": _v(iface.get("role")),
            "ips": _iface_ips(iface),
            "remote": remote,
        })
    asn = _node(node.get("asn"))
    return {
        "name": _v(node["name"]),
        "role": _v(node.get("role")),
        "platform": _v(platform.get("name")),
        "capabilities": {
            _v(c["name"]) for c in _edges(platform.get("capabilities"))
        },
        "asn": _v(asn["asn"]) if asn else None,
        "interfaces": interfaces,
    }


def parse_contracts(data):
    contracts = []
    for node in _edges(data["IntentRoutingContract"]):
        tenant = _node(node.get("tenant"))
        vrf = None
        if tenant:
            vrfs = _edges(tenant.get("vrfs"))
            if vrfs:
                v = vrfs[0]
                import_rt = _node(v.get("import_rt"))
                export_rt = _node(v.get("export_rt"))
                vrf = {
                    "name": _v(v["name"]),
                    "rd": _v(v.get("vrf_rd")),
                    "import_rt": _v(import_rt["name"]) if import_rt else None,
                    "export_rt": _v(export_rt["name"]) if export_rt else None,
                }
        contracts.append({
            "name": _v(node["name"]),
            "role": _v(node["role"]),
            "peer_asn": _v(node["peer_asn"]),
            "afi_safis": _v(node["afi_safis"]) or [],
            "max_prefixes_ipv4": _v(node.get("max_prefixes_ipv4")),
            "max_prefixes_ipv6": _v(node.get("max_prefixes_ipv6")),
            "local_preference": _v(node.get("local_preference")),
            "require_communities": _v(node.get("require_communities")) or [],
            "attach_communities": _v(node.get("attach_communities")) or [],
            "zone": _v((_node(node.get("zone")) or {}).get("name")),
            "export_deny_zones": [
                _v(z["name"]) for z in _edges(node.get("export_deny_zones"))
            ],
            "export_tenants": [
                _v(t["name"]) for t in _edges(node.get("export_tenants"))
            ],
            "tenant": _v(tenant["name"]) if tenant else None,
            "tenant_community": _v(tenant.get("tenant_community")) if tenant else None,
            "vrf": vrf,
            "pe_devices": _parse_contract_devices(node.get("pe_devices")),
            "ce_devices": _parse_contract_devices(node.get("ce_devices")),
            "allowed_prefixes": [
                _v(p["prefix"]) for p in _edges(node.get("allowed_prefixes"))
            ],
            "invariants": [
                _v(i["invariant_type"]) for i in _edges(node.get("invariants"))
            ],
        })
    return contracts


def _parse_contract_devices(rel):
    devices = []
    for node in _edges(rel):
        asn = _node(node.get("asn"))
        devices.append({
            "name": _v(node["name"]),
            # the far-side ASN: renderers whose CLI has no `remote-as
            # external` (IOS-XE) read it off the modeled peer device
            "asn": _v(asn["asn"]) if asn else None,
            "interfaces": [
                {"name": _v(i["name"]), "ips": _iface_ips(i)}
                for i in _edges(node.get("interfaces"))
            ],
        })
    return devices


def parse_security(data, device_role):
    policies = []
    for node in _edges(data.get("IntentSecurityPolicy") or {"edges": []}):
        if _v(node["attach_device_role"]) != device_role:
            continue
        rules = []
        for rule in _edges(node.get("rules")):
            src_zone = _node(rule.get("src_zone"))
            rules.append({
                "name": _v(rule["name"]),
                "index": _v(rule["index"]),
                "action": _v(rule["action"]),
                "protocol": _v(rule["protocol"]),
                "dst_ports": _v(rule.get("dst_ports")) or [],
                "src_zone": _v(src_zone["name"]) if src_zone else None,
                "dst_prefixes": [
                    _v(p["prefix"]) for p in _edges(rule.get("dst_prefixes"))
                ],
            })
        rules.sort(key=lambda r: r["index"] or 0)
        policies.append({
            "name": _v(node["name"]),
            "ddos_profile": _v(node["ddos_profile"]),
            "attach_interface_role": _v(node["attach_interface_role"]),
            "tenant": _v((_node(node.get("tenant")) or {}).get("name")),
            "rules": rules,
        })
    return policies


def loopback_iface(interfaces):
    """First loopback-ish interface carrying an address."""
    for iface in interfaces:
        name = (iface["name"] or "").lower()
        if name.startswith(LOOPBACK_PREFIXES) and iface["ips"]:
            return iface
    return None


def loopback_ip(interfaces):
    """First loopback-ish interface address, without the mask."""
    iface = loopback_iface(interfaces)
    return iface["ips"][0].split("/")[0] if iface else None


def remote_asn(contract, side, device_name):
    """ASN of a named device on one side of a contract.

    A contract's ``peer_asn`` is written from the provider's point of view,
    so a CE cannot read the provider's ASN off it. Renderers whose CLI has
    no ``remote-as external`` (IOS-XE) read it off the modeled peer device
    instead - still derived, never a new intent field.
    """
    for device in contract[side]:
        if device["name"] == device_name:
            return device["asn"]
    return None


def is_customer_side(device, contracts):
    """True when the device only ever sits on the ``ce_devices`` side.

    Dispatching on this rather than on ``role`` keeps the renderer honest:
    the PEs are ce_devices of the core contract (they are RR *clients*) and
    pe_devices of the tenant contract, so only a device that is never a
    pe_device is a customer/peer endpoint.
    """
    name = device["name"]
    on_ce = any(name in {d["name"] for d in c["ce_devices"]} for c in contracts)
    on_pe = any(name in {d["name"] for d in c["pe_devices"]} for c in contracts)
    return on_ce and not on_pe


def edge_sessions(device, contract, local_side):
    """Derive BGP sessions from modeled cabling.

    Walks the device's interfaces: any interface cabled to a device on
    the *other* side of the contract yields one session (local ip,
    neighbor ip). No session objects are modeled - sessions are a
    consequence of the contract plus the topology.
    """
    remote_side = "ce_devices" if local_side == "pe_devices" else "pe_devices"
    local_names = {d["name"] for d in contract[local_side]}
    remote_names = {d["name"] for d in contract[remote_side]}
    if device["name"] not in local_names:
        return []

    sessions = []
    for iface in device["interfaces"]:
        remote = iface["remote"]
        if not remote or remote["device"] not in remote_names:
            continue
        if not iface["ips"] or not remote["ips"]:
            continue
        sessions.append({
            "interface": iface["name"],
            "local_ip": iface["ips"][0].split("/")[0],
            "neighbor_ip": remote["ips"][0].split("/")[0],
            "neighbor_device": remote["device"],
        })
    return sessions


def denied_communities(contracts, peering_contract):
    """Tenant communities that must be filtered outbound on a peering
    contract: every no-leak tenant that denies this contract's zone and is
    not explicitly exported here."""
    denied = []
    for other in contracts:
        if not other["tenant"] or "no_leak" not in other["invariants"]:
            continue
        if peering_contract["zone"] not in other["export_deny_zones"]:
            continue
        if other["tenant"] in peering_contract["export_tenants"]:
            continue
        if other["tenant_community"]:
            denied.append((other["tenant"], other["tenant_community"]))
    return sorted(set(denied))


def _wildcard(prefix):
    net = ipaddress.ip_network(prefix)
    return f"{net.network_address} {net.hostmask}"


# --------------------------------------------------------------------------
# Renderers - one per platform, freely replaceable
# --------------------------------------------------------------------------

def render_cisco_iosxe(device, contracts, policies):
    """IOS-XE has two shapes in this lab.

    Provider edge: VRFs, vpnv4 to the route reflector, per-tenant address
    families. Customer edge: advertise exactly the prefixes the contract
    authorizes and nothing else. Which one is rendered follows from the
    contract graph, not from a flag on the device.
    """
    if is_customer_side(device, contracts):
        return _iosxe_customer_edge(device, contracts)
    return _iosxe_provider_edge(device, contracts, policies)


def _iosxe_customer_edge(device, contracts):
    lines = [
        "! Compiled by InfraHub - intent artifact, do not hand-edit",
        f"hostname {device['name']}",
        "ip cef",
        "!",
    ]

    for iface in device["interfaces"]:
        if not iface["ips"]:
            continue
        addr = ipaddress.ip_interface(iface["ips"][0])
        lines.append(f"interface {iface['name']}")
        if iface["description"]:
            lines.append(f" description {iface['description']}")
        lines += [
            f" ip address {addr.ip} {addr.netmask}",
            " no shutdown",
            "!",
        ]

    my_contracts = [
        c for c in contracts
        if device["name"] in {d["name"] for d in c["ce_devices"]}
        and c["role"] in ("customer_edge", "internet_peering", "transit")
    ]

    # anchor routes so the `network` statements have something to advertise
    v4_prefixes = []
    for c in my_contracts:
        for prefix in c["allowed_prefixes"]:
            net = ipaddress.ip_network(prefix)
            if net.version == 4:
                v4_prefixes.append(net)
    for net in v4_prefixes:
        lines.append(f"ip route {net.network_address} {net.netmask} Null0")
    lines.append("!")

    if not device["asn"]:
        return "\n".join(lines) + "\n"

    lines.append(f"router bgp {device['asn']}")
    lines.append(" bgp log-neighbor-changes")

    # neighbors are declared globally on IOS-XE, activated per AF below
    sessions = []
    for c in my_contracts:
        for session in edge_sessions(device, c, "ce_devices"):
            asn = remote_asn(c, "pe_devices", session["neighbor_device"])
            lines.append(f" neighbor {session['neighbor_ip']} remote-as {asn}")
            sessions.append(session)
    lines.append(" !")

    lines.append(" address-family ipv4")
    for net in v4_prefixes:
        lines.append(f"  network {net.network_address} mask {net.netmask}")
    for session in sessions:
        lines.append(f"  neighbor {session['neighbor_ip']} activate")
    lines += [" exit-address-family", "!"]

    return "\n".join(lines) + "\n"


def _iosxe_provider_edge(device, contracts, policies):
    lines = [
        f"! Compiled by InfraHub - intent artifact, do not hand-edit",
        f"hostname {device['name']}",
        "ip cef",
        "mpls label protocol ldp",
        "!",
    ]

    # VRFs for tenant contracts attached here
    my_edge = [
        c for c in contracts
        if c["role"] == "customer_edge" and c["vrf"]
        and device["name"] in {d["name"] for d in c["pe_devices"]}
    ]
    for c in my_edge:
        vrf = c["vrf"]
        lines += [
            f"vrf definition {vrf['name']}",
            f" rd {vrf['rd']}",
            " address-family ipv4",
            f"  route-target import {vrf['import_rt']}",
            f"  route-target export {vrf['export_rt']}",
            " exit-address-family",
            "!",
        ]

    # Interfaces
    cust_vrf_by_neighbor_dev = {}
    for c in my_edge:
        for d in c["ce_devices"]:
            cust_vrf_by_neighbor_dev[d["name"]] = c["vrf"]["name"]

    for iface in device["interfaces"]:
        if not iface["ips"]:
            continue
        addr = ipaddress.ip_interface(iface["ips"][0])
        lines.append(f"interface {iface['name']}")
        if iface["description"]:
            lines.append(f" description {iface['description']}")
        remote_dev = iface["remote"]["device"] if iface["remote"] else None
        vrf_name = cust_vrf_by_neighbor_dev.get(remote_dev)
        if vrf_name and iface["role"] == "cust":
            lines.append(f" vrf forwarding {vrf_name}")
        lines.append(f" ip address {addr.ip} {addr.netmask}")
        if iface["role"] == "core":
            lines += [" ip ospf 1 area 0", " mpls ip"]
        acl = _iosxe_acl_name(policies, iface["role"])
        if acl:
            lines.append(f" ip access-group {acl} in")
        lines += [" no shutdown", "!"]

    lines += [
        "router ospf 1",
        f" router-id {loopback_ip(device['interfaces'])}",
        "!",
    ]

    # Security ACLs
    for policy in policies:
        lines += _iosxe_acl(policy)

    # BGP
    lo = loopback_ip(device["interfaces"])
    lines.append(f"router bgp {device['asn']}")
    lines.append(f" bgp router-id {lo}")

    # iBGP vpnv4 to route reflector(s) - derived from the core contract
    for c in contracts:
        if c["role"] != "core":
            continue
        if device["name"] in {d["name"] for d in c["ce_devices"]}:
            for rr in c["pe_devices"]:
                rr_lo = loopback_ip(
                    [{"name": i["name"], "ips": i["ips"]} for i in rr["interfaces"]]
                )
                lines += [
                    f" neighbor {rr_lo} remote-as {c['peer_asn']}",
                    f" neighbor {rr_lo} update-source Loopback0",
                ]
                lines += [
                    " address-family vpnv4",
                    f"  neighbor {rr_lo} activate",
                    f"  neighbor {rr_lo} send-community extended",
                    " exit-address-family",
                ]

    # per-tenant VRF address families
    for c in my_edge:
        pfx_list = f"{c['name'].upper()}-IN-V4"
        rm_in = f"{c['name'].upper()}-IN"
        for session in edge_sessions(device, c, "pe_devices"):
            lines += [
                f" address-family ipv4 vrf {c['vrf']['name']}",
                f"  neighbor {session['neighbor_ip']} remote-as {c['peer_asn']}",
                f"  neighbor {session['neighbor_ip']} activate",
                f"  neighbor {session['neighbor_ip']} send-community both",
                f"  neighbor {session['neighbor_ip']} route-map {rm_in} in",
            ]
            if c["max_prefixes_ipv4"]:
                lines.append(
                    f"  neighbor {session['neighbor_ip']} maximum-prefix "
                    f"{c['max_prefixes_ipv4']} 80 warning-only"
                )
            lines.append(" exit-address-family")
    lines.append("!")

    # Prefix authorization + attribute policy
    for c in my_edge:
        pfx_list = f"{c['name'].upper()}-IN-V4"
        rm_in = f"{c['name'].upper()}-IN"
        seq = 5
        for prefix in c["allowed_prefixes"]:
            if ipaddress.ip_network(prefix).version != 4:
                continue
            lines.append(f"ip prefix-list {pfx_list} seq {seq} permit {prefix}")
            seq += 5
        communities = " ".join(c["require_communities"] + c["attach_communities"])
        lines += [
            f"route-map {rm_in} permit 10",
            f" match ip address prefix-list {pfx_list}",
        ]
        if communities:
            lines.append(f" set community {communities} additive")
        if c["local_preference"]:
            lines.append(f" set local-preference {c['local_preference']}")
        lines += [f"route-map {rm_in} deny 20", "!"]

    return "\n".join(lines) + "\n"


def _iosxe_acl_name(policies, iface_role):
    for policy in policies:
        if policy["attach_interface_role"] == iface_role and policy["rules"]:
            return policy["name"].upper()
    return None


def _iosxe_acl(policy):
    if not policy["rules"]:
        return []
    lines = [f"ip access-list extended {policy['name'].upper()}"]
    if policy["ddos_profile"] != "none":
        lines.append(f" remark ddos-profile: {policy['ddos_profile']}")
    for rule in policy["rules"]:
        proto = "ip" if rule["protocol"] == "any" else rule["protocol"]
        action = "permit" if rule["action"] == "allow" else "deny"
        lines.append(f" remark {rule['name']} (src-zone: {rule['src_zone'] or 'any'})")
        dsts = rule["dst_prefixes"] or ["0.0.0.0/0"]
        for dst in dsts:
            if ipaddress.ip_network(dst).version != 4:
                continue
            dst_part = _wildcard(dst)
            if rule["dst_ports"] and proto in ("tcp", "udp"):
                for port in rule["dst_ports"]:
                    lines.append(f" {action} {proto} any {dst_part} eq {port}")
            else:
                lines.append(f" {action} {proto} any {dst_part}")
    lines += [" permit ip any any", "!"]
    return lines


def render_arista_eos(device, contracts, policies):
    lines = [
        "! Compiled by InfraHub - intent artifact, do not hand-edit",
        f"hostname {device['name']}",
        "service routing protocols model multi-agent",
        "ip routing",
        "mpls ip",
        "!",
    ]

    my_edge = [
        c for c in contracts
        if c["role"] == "customer_edge" and c["vrf"]
        and device["name"] in {d["name"] for d in c["pe_devices"]}
    ]
    my_peering = [
        c for c in contracts
        if c["role"] in ("internet_peering", "transit")
        and device["name"] in {d["name"] for d in c["pe_devices"]}
    ]

    for c in my_edge:
        vrf = c["vrf"]
        lines += [f"vrf instance {vrf['name']}", "!",
                  f"ip routing vrf {vrf['name']}", "!"]

    cust_vrf_by_neighbor_dev = {}
    for c in my_edge:
        for d in c["ce_devices"]:
            cust_vrf_by_neighbor_dev[d["name"]] = c["vrf"]["name"]

    for iface in device["interfaces"]:
        if not iface["ips"]:
            continue
        lines.append(f"interface {iface['name']}")
        if iface["description"]:
            lines.append(f" description {iface['description']}")
        if not iface["name"].lower().startswith("loopback"):
            lines.append(" no switchport")
        remote_dev = iface["remote"]["device"] if iface["remote"] else None
        vrf_name = cust_vrf_by_neighbor_dev.get(remote_dev)
        if vrf_name and iface["role"] == "cust":
            lines.append(f" vrf {vrf_name}")
        lines.append(f" ip address {iface['ips'][0]}")
        if iface["role"] == "core":
            lines += [" ip ospf area 0.0.0.0", " mpls ip"]
        acl = _iosxe_acl_name(policies, iface["role"])
        if acl:
            lines.append(f" ip access-group {acl} in")
        lines.append("!")

    lo = loopback_ip(device["interfaces"])
    lines += [
        "router ospf 1",
        f" router-id {lo}",
        " passive-interface Loopback0",
        "!",
        "mpls ldp",
        f" router-id {lo}",
        " no shutdown",
        "!",
    ]

    for policy in policies:
        lines += _eos_acl(policy)

    # community-lists for leak protection on peering contracts
    community_lists = [
        f"ip community-list DENY-TENANT-{tenant.upper()} permit {community}"
        for c in my_peering
        for tenant, community in denied_communities(contracts, c)
    ]
    if community_lists:
        lines += community_lists + ["!"]

    lines.append(f"router bgp {device['asn']}")
    lines.append(f" router-id {lo}")

    for c in contracts:
        if c["role"] != "core":
            continue
        if device["name"] in {d["name"] for d in c["ce_devices"]}:
            for rr in c["pe_devices"]:
                rr_lo = loopback_ip(
                    [{"name": i["name"], "ips": i["ips"]} for i in rr["interfaces"]]
                )
                lines += [
                    f" neighbor {rr_lo} remote-as {c['peer_asn']}",
                    f" neighbor {rr_lo} update-source Loopback0",
                    f" neighbor {rr_lo} send-community extended",
                    " address-family vpn-ipv4",
                    f"  neighbor {rr_lo} activate",
                    " !",
                ]

    # internet peering / transit sessions in the default VRF
    for c in my_peering:
        rm_in = f"{c['name'].upper()}-IN"
        rm_out = f"{c['name'].upper()}-OUT"
        for session in edge_sessions(device, c, "pe_devices"):
            lines += [
                f" neighbor {session['neighbor_ip']} remote-as {c['peer_asn']}",
                f" neighbor {session['neighbor_ip']} send-community",
                f" neighbor {session['neighbor_ip']} route-map {rm_in} in",
                f" neighbor {session['neighbor_ip']} route-map {rm_out} out",
            ]
            if c["max_prefixes_ipv4"]:
                lines.append(
                    f" neighbor {session['neighbor_ip']} maximum-routes "
                    f"{c['max_prefixes_ipv4']}"
                )
            lines += [
                " address-family ipv4",
                f"  neighbor {session['neighbor_ip']} activate",
                " !",
            ]

    # tenant VRF address families
    for c in my_edge:
        rm_in = f"{c['name'].upper()}-IN"
        lines.append(f" vrf {c['vrf']['name']}")
        lines.append(f"  rd {c['vrf']['rd']}")
        lines.append(f"  route-target import vpn-ipv4 {c['vrf']['import_rt'].replace('target:', '')}")
        lines.append(f"  route-target export vpn-ipv4 {c['vrf']['export_rt'].replace('target:', '')}")
        for session in edge_sessions(device, c, "pe_devices"):
            lines += [
                f"  neighbor {session['neighbor_ip']} remote-as {c['peer_asn']}",
                f"  neighbor {session['neighbor_ip']} send-community",
                f"  neighbor {session['neighbor_ip']} route-map {rm_in} in",
            ]
            if c["max_prefixes_ipv4"]:
                lines.append(
                    f"  neighbor {session['neighbor_ip']} maximum-routes "
                    f"{c['max_prefixes_ipv4']}"
                )
        lines.append(" !")
    lines.append("!")

    # route-maps + prefix-lists
    for c in my_edge:
        pfx_list = f"{c['name'].upper()}-IN-V4"
        rm_in = f"{c['name'].upper()}-IN"
        seq = 5
        for prefix in c["allowed_prefixes"]:
            if ipaddress.ip_network(prefix).version != 4:
                continue
            lines.append(f"ip prefix-list {pfx_list} seq {seq} permit {prefix}")
            seq += 5
        communities = " ".join(c["require_communities"] + c["attach_communities"])
        lines += [
            f"route-map {rm_in} permit 10",
            f" match ip address prefix-list {pfx_list}",
        ]
        if communities:
            lines.append(f" set community {communities} additive")
        if c["local_preference"]:
            lines.append(f" set local-preference {c['local_preference']}")
        lines += [f"route-map {rm_in} deny 20", "!"]

    for c in my_peering:
        pfx_list = f"{c['name'].upper()}-IN-V4"
        rm_in = f"{c['name'].upper()}-IN"
        rm_out = f"{c['name'].upper()}-OUT"
        seq = 5
        for prefix in c["allowed_prefixes"]:
            if ipaddress.ip_network(prefix).version != 4:
                continue
            lines.append(f"ip prefix-list {pfx_list} seq {seq} permit {prefix}")
            seq += 5
        lines += [
            f"route-map {rm_in} permit 10",
            f" match ip address prefix-list {pfx_list}",
            f"route-map {rm_in} deny 20",
        ]
        # THE no-leak materialization: tenant communities denied outbound
        seq = 10
        for tenant, _community in denied_communities(contracts, c):
            lines += [
                f"route-map {rm_out} deny {seq}",
                f" match community DENY-TENANT-{tenant.upper()}",
            ]
            seq += 10
        lines += [f"route-map {rm_out} permit {seq}", "!"]

    return "\n".join(lines) + "\n"


def _eos_acl(policy):
    if not policy["rules"]:
        return []
    lines = [f"ip access-list {policy['name'].upper()}"]
    seq = 10
    for rule in policy["rules"]:
        proto = "ip" if rule["protocol"] == "any" else rule["protocol"]
        action = "permit" if rule["action"] == "allow" else "deny"
        dsts = rule["dst_prefixes"] or ["0.0.0.0/0"]
        for dst in dsts:
            if ipaddress.ip_network(dst).version != 4:
                continue
            if rule["dst_ports"] and proto in ("tcp", "udp"):
                for port in rule["dst_ports"]:
                    lines.append(f" {seq} {action} {proto} any {dst} eq {port}")
                    seq += 10
            else:
                lines.append(f" {seq} {action} {proto} any {dst}")
                seq += 10
    lines += [f" {seq} permit ip any any", "!"]
    return lines


def render_juniper_junos(device, contracts, policies):
    lo_iface = loopback_iface(device["interfaces"])
    lo_name = lo_iface["name"] if lo_iface else "lo0"
    lo = loopback_ip(device["interfaces"])

    lines = [
        "# Compiled by InfraHub - intent artifact, do not hand-edit",
        "# Apply with: configure; load set terminal < this file; commit",
        f"set system host-name {device['name']}",
    ]

    for iface in device["interfaces"]:
        if not iface["ips"]:
            continue
        name = iface["name"]
        if iface["description"]:
            lines.append(f'set interfaces {name} description "{iface["description"]}"')
        lines.append(
            f"set interfaces {name} unit 0 family inet address {iface['ips'][0]}"
        )
        if iface["role"] == "core":
            lines.append(f"set interfaces {name} unit 0 family mpls")

    lines += [
        f"set routing-options router-id {lo}",
        f"set routing-options autonomous-system {device['asn']}",
    ]

    core_ifaces = [
        i for i in device["interfaces"] if i["role"] == "core" and i["ips"]
    ]
    for iface in core_ifaces:
        lines.append(
            f"set protocols ospf area 0.0.0.0 interface {iface['name']}.0 "
            f"interface-type p2p"
        )
    lines.append(f"set protocols ospf area 0.0.0.0 interface {lo_name}.0 passive")
    for iface in core_ifaces:
        lines.append(f"set protocols mpls interface {iface['name']}.0")
        lines.append(f"set protocols ldp interface {iface['name']}.0")

    # Route-reflector role: clients derived from the core contract
    for c in contracts:
        if c["role"] != "core":
            continue
        if device["name"] not in {d["name"] for d in c["pe_devices"]}:
            continue
        lines += [
            "set protocols bgp group rr-clients type internal",
            f"set protocols bgp group rr-clients local-address {lo}",
            f"set protocols bgp group rr-clients cluster {lo}",
        ]
        if "vpn-ipv4" in c["afi_safis"]:
            lines.append(
                "set protocols bgp group rr-clients family inet-vpn unicast"
            )
        for client in c["ce_devices"]:
            client_lo = loopback_ip(client["interfaces"])
            lines.append(
                f"set protocols bgp group rr-clients neighbor {client_lo}"
            )

    return "\n".join(lines) + "\n"


def render_nokia_srlinux(device, contracts, policies):
    lo = loopback_ip(device["interfaces"])
    lines = [
        "# Compiled by InfraHub - intent artifact, do not hand-edit",
        "# Apply with: sr_cli --candidate-mode < this file, then commit",
    ]

    for iface in device["interfaces"]:
        if not iface["ips"]:
            continue
        name = iface["name"]
        if name.lower().startswith("system"):
            lines.append(
                f"set / interface system0 admin-state enable subinterface 0 "
                f"ipv4 admin-state enable address {iface['ips'][0]}"
            )
            lines.append("set / network-instance default interface system0.0")
        else:
            lines.append(f"set / interface {name} admin-state enable")
            lines.append(
                f"set / interface {name} subinterface 0 ipv4 admin-state enable "
                f"address {iface['ips'][0]}"
            )
            lines.append(f"set / network-instance default interface {name}.0")

    lines += [
        "set / network-instance default protocols ospf instance main version ospf-v2",
        f"set / network-instance default protocols ospf instance main router-id {lo}",
        "set / network-instance default protocols ospf instance main admin-state enable",
    ]
    for iface in device["interfaces"]:
        if iface["role"] == "core" and iface["ips"]:
            lines.append(
                f"set / network-instance default protocols ospf instance main "
                f"area 0.0.0.0 interface {iface['name']}.0 interface-type point-to-point"
            )
    lines.append(
        "set / network-instance default protocols ospf instance main "
        "area 0.0.0.0 interface system0.0 passive true"
    )
    for iface in device["interfaces"]:
        if iface["role"] == "core" and iface["ips"]:
            lines.append(
                f"set / network-instance default protocols ldp discovery interfaces "
                f"interface {iface['name']}.0 ipv4 admin-state enable"
            )
    lines.append("set / network-instance default protocols ldp admin-state enable")

    lines += [
        f"set / network-instance default protocols bgp autonomous-system {device['asn']}",
        f"set / network-instance default protocols bgp router-id {lo}",
        "set / network-instance default protocols bgp afi-safi ipv4-unicast admin-state enable",
    ]

    # Route-reflector role: clients derived from the core contract
    for c in contracts:
        if c["role"] != "core":
            continue
        if device["name"] not in {d["name"] for d in c["pe_devices"]}:
            continue
        lines += [
            "set / network-instance default protocols bgp afi-safi vpn-ipv4 admin-state enable",
            "set / network-instance default protocols bgp group rr-clients peer-as "
            + str(c["peer_asn"]),
            f"set / network-instance default protocols bgp group rr-clients "
            f"route-reflector cluster-id {lo}",
            "set / network-instance default protocols bgp group rr-clients "
            "route-reflector client true",
            "set / network-instance default protocols bgp group rr-clients "
            "afi-safi vpn-ipv4 admin-state enable",
            "set / network-instance default protocols bgp group rr-clients "
            "transport local-address " + lo,
        ]
        for client in c["ce_devices"]:
            client_lo = loopback_ip(
                [{"name": i["name"], "ips": i["ips"]} for i in client["interfaces"]]
            )
            lines.append(
                f"set / network-instance default protocols bgp neighbor {client_lo} "
                f"peer-group rr-clients"
            )

    return "\n".join(lines) + "\n"


def render_frr(device, contracts, policies):
    lines = [
        "! Compiled by InfraHub - intent artifact, do not hand-edit",
        "frr defaults traditional",
        f"hostname {device['name']}",
        "!",
    ]

    for iface in device["interfaces"]:
        if not iface["ips"]:
            continue
        lines.append(f"interface {iface['name']}")
        if iface["description"]:
            lines.append(f" description {iface['description']}")
        lines.append(f" ip address {iface['ips'][0]}")
        lines.append("!")

    # CE / peer role: advertise exactly the prefixes the contract authorizes
    my_contracts = [
        c for c in contracts
        if device["name"] in {d["name"] for d in c["ce_devices"]}
        and c["role"] in ("customer_edge", "internet_peering", "transit")
    ]

    # anchor routes so `network` statements activate
    for c in my_contracts:
        for prefix in c["allowed_prefixes"]:
            if ipaddress.ip_network(prefix).version == 4:
                lines.append(f"ip route {prefix} blackhole")
    lines.append("!")

    if device["asn"]:
        lines.append(f"router bgp {device['asn']}")
        lo = loopback_ip(device["interfaces"])
        if lo:
            lines.append(f" bgp router-id {lo}")
        for c in my_contracts:
            # the provider-side ASN is not part of a CE-scoped contract view;
            # eBGP with `remote-as external` keeps the CE config mechanism-free
            for session in edge_sessions(device, c, "ce_devices"):
                lines.append(
                    f" neighbor {session['neighbor_ip']} remote-as external"
                )
        lines.append(" address-family ipv4 unicast")
        for c in my_contracts:
            for prefix in c["allowed_prefixes"]:
                if ipaddress.ip_network(prefix).version == 4:
                    lines.append(f"  network {prefix}")
            for session in edge_sessions(device, c, "ce_devices"):
                lines.append(f"  neighbor {session['neighbor_ip']} activate")
        lines += [" exit-address-family", "!"]

    lines += ["line vty", "!"]
    return "\n".join(lines) + "\n"


# nokia_srlinux has no device today; it stays registered so re-platforming a
# node onto SR Linux is one field in the SoT and nothing here.
RENDERERS = {
    "cisco_iosxe": render_cisco_iosxe,
    "arista_eos": render_arista_eos,
    "juniper_junos": render_juniper_junos,
    "nokia_srlinux": render_nokia_srlinux,
    "frr": render_frr,
}


class DeviceConfigTransform(InfrahubTransform):
    query = "device_config"

    async def transform(self, data):
        device = parse_device(data)
        contracts = parse_contracts(data)
        policies = parse_security(data, "pe" if device["role"] == "edge" else device["role"])

        renderer = RENDERERS.get(device["platform"] or "")
        if renderer is None:
            return (
                f"# No renderer registered for platform "
                f"'{device['platform']}' (device {device['name']}).\n"
                f"# Intent stays stable - add a renderer, not a schema field.\n"
            )
        return renderer(device, contracts, policies)
