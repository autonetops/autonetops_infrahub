#!/usr/bin/env python3
"""CLI-scrape telemetry collector for platforms without gNMI.

The intent model requires bgp_session_state / bgp_route_count evidence
from every attachment PE. The Cisco IOS-XE image in this lab exposes no
gNMI, so its platform claims only the ``ssh_cli`` telemetry capability -
and the observability compiler emits a telegraf [[inputs.exec]] that
runs this script instead of a gNMI subscription.

Emits influx line protocol on stdout, normalized to the same metric
names the gNMI pipeline produces:

    bgp_session,device=pe-emea-01,neighbor=10.84.255.2,contract=custc-ce-to-pe \
        state_established=1i,prefixes_received=3i,prefixes_sent=12i

Credentials come from SSH_USER / SSH_PASSWORD environment variables
(set on the telegraf container), never from arguments or the SoT.
"""

import argparse
import os
import re
import sys

try:
    from netmiko import ConnectHandler
except ImportError:
    print("cli_bgp_telemetry: netmiko not installed", file=sys.stderr)
    sys.exit(1)

NETMIKO_TYPES = {
    "cisco_iosxe": "cisco_ios",
    "frr": "linux",
}

# 'show bgp * summary' neighbor line:
#   10.84.255.2  4  65123  123  456  10  0  0  01:23:45  3
NEIGHBOR_RE = re.compile(
    r"^(?P<neighbor>\d+\.\d+\.\d+\.\d+)\s+4\s+(?P<asn>\d+)"
    r"(?:\s+\S+){6}\s+(?P<state>\S+)\s*$"
)


def collect_cisco(conn):
    """Yield (neighbor, established, prefixes_received) tuples from every
    address family, VRFs included."""
    output = conn.send_command("show bgp all summary")
    for line in output.splitlines():
        match = NEIGHBOR_RE.match(line.strip())
        if not match:
            continue
        state = match.group("state")
        if state.isdigit():
            yield match.group("neighbor"), 1, int(state)
        else:
            yield match.group("neighbor"), 0, 0


def advertised_count(conn, neighbor):
    output = conn.send_command(
        f"show bgp all neighbors {neighbor} advertised-routes | count ^ ?\\*"
    )
    match = re.search(r"= (\d+)", output)
    return int(match.group(1)) if match else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--platform", default="cisco_iosxe")
    parser.add_argument("--contract", default="")
    args = parser.parse_args()

    conn = ConnectHandler(
        device_type=NETMIKO_TYPES.get(args.platform, "cisco_ios"),
        host=args.host,
        username=os.environ.get("SSH_USER", "admin"),
        password=os.environ.get("SSH_PASSWORD", "admin"),
        fast_cli=False,
    )
    try:
        for neighbor, established, received in collect_cisco(conn):
            sent = advertised_count(conn, neighbor) if established else 0
            tags = f"device={args.device},neighbor={neighbor}"
            if args.contract:
                tags += f",contract={args.contract}"
            fields = (
                f"state_established={established}i,"
                f"prefixes_received={received}i"
            )
            if sent is not None:
                fields += f",prefixes_sent={sent}i"
            print(f"bgp_session,{tags} {fields}")
    finally:
        conn.disconnect()


if __name__ == "__main__":
    main()
