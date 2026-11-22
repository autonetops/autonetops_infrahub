"""Link topology derived from IPAM, not from cables.

DcimCable was removed from the schema (2026-11-22). Adjacency is now read
off the addressing plan: two interfaces are linked when they are the ONLY
two interfaces, on two different devices, holding an address in the same
subnet (a /30, /31 or any other point-to-point allocation). Shared LANs,
such as a management /24, match more than two interfaces and therefore
yield no link. That is deliberate: a BGP neighbor must be the single far
end of a connected subnet.

Every query that needs topology lists the addresses once, at the root:

    IpamIPAddress {
      edges { node {
        address { value }
        interface { node {
          ... on DcimInterface {
            name { value }
            device { node { name { value } } }
          }
        } }
      } }
    }
"""

import ipaddress
from collections import defaultdict


def _v(attr):
    return attr.get("value") if attr else None


def _node(rel):
    return rel.get("node") if rel else None


def address_index(data):
    """IpamIPAddress root result -> [(device, interface, address CIDR)]."""
    rows = []
    for edge in (data.get("IpamIPAddress") or {}).get("edges", []):
        ip = edge["node"]
        iface = _node(ip.get("interface"))
        if not iface:
            continue
        device = _node(iface.get("device"))
        if not device or not iface.get("name"):
            continue
        rows.append((_v(device["name"]), _v(iface["name"]), _v(ip["address"])))
    return rows


def subnet_members(rows):
    """network -> {(device, interface): [addresses]} for every addressed
    interface. Host routes (/32, /128) never form links."""
    members = defaultdict(lambda: defaultdict(list))
    for device, iface, address in rows:
        try:
            net = ipaddress.ip_interface(address).network
        except ValueError:
            continue
        if net.num_addresses == 1:
            continue
        members[net][(device, iface)].append(address)
    return members


def link_peers(rows):
    """(device, interface) -> remote {device, interface, ips} for every
    point-to-point link in the addressing plan."""
    by_iface = defaultdict(list)
    for device, iface, address in rows:
        by_iface[(device, iface)].append(address)

    peers = {}
    for ends in subnet_members(rows).values():
        if len(ends) != 2:
            continue
        a, b = ends
        if a[0] == b[0]:
            continue
        peers[a] = {"device": b[0], "interface": b[1], "ips": by_iface[b]}
        peers[b] = {"device": a[0], "interface": a[1], "ips": by_iface[a]}
    return peers

