"""Intent compiler: telemetry collector configuration.

This is where the capability taxonomy earns its keep. The same
ObservabilitySignal intent compiles to a *different* collector per
device, selected by what the device's platform claims:

    gnmi     -> telegraf [[inputs.gnmi]] subscription (paths per platform)
    ssh_cli  -> telegraf [[inputs.exec]] running the CLI-scrape script
                (the Cisco IOS-XE path - no gNMI on that platform)
    (always) -> telegraf [[inputs.ping]] for the fleet-wide
                device_reachability signal via the OOB address

Signals are scoped to a contract's PE devices, and both PEs are cEOS, so
today every scoped signal compiles to gNMI. The CLI-scrape arm is what the
gNMI-less Cisco CEs would get the moment a signal is pointed at
``ce_devices`` - one query field, no intent change.

Nothing in the intent names a collector. Change a platform's
capabilities in the SoT and the compiled observability changes with it -
no schema change, no template fork.

Output: a telegraf config *fragment* (inputs + processors only) dropped
into telegraf.d/; the static telegraf.conf owns the outputs section.
"""

from infrahub_sdk.transforms import InfrahubTransform

# gNMI subscription paths per platform per signal - renderer-owned
# vendor knowledge that must never contaminate the intent model.
GNMI_PATHS = {
    "arista_eos": {
        "bgp_session_state": [
            "/network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor/state/session-state",
        ],
        "bgp_route_count": [
            "/network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor/afi-safis/afi-safi/state/prefixes",
        ],
        "policy_export_violation": [
            "/network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor/afi-safis/afi-safi/state/prefixes",
        ],
        "interface_errors": [
            "/interfaces/interface/state/counters",
        ],
    },
    "juniper_junos": {
        "bgp_session_state": [
            "/network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor/state/session-state",
        ],
        "bgp_route_count": [
            "/network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor/afi-safis/afi-safi/state/prefixes",
        ],
        "interface_errors": [
            "/interfaces/interface/state/counters",
        ],
    },
    "nokia_srlinux": {
        "bgp_session_state": [
            "/network-instance[name=default]/protocols/bgp/neighbor[peer-address=*]/session-state",
        ],
        "bgp_route_count": [
            "/network-instance[name=default]/protocols/bgp/neighbor[peer-address=*]/afi-safi[afi-safi-name=*]/received-routes",
        ],
        "interface_errors": [
            "/interface[name=*]/statistics",
        ],
    },
}

# gNMI listener port and TLS posture per platform - renderer knowledge, like
# the paths above. Junos serves gNMI from jsd on 9339; SR Linux serves it on
# 57400 behind a self-signed profile, so the collector must speak TLS and
# skip verification.
GNMI_TRANSPORT = {
    "arista_eos": {"port": 6030, "tls": "  tls_enable = false"},
    "juniper_junos": {"port": 9339, "tls": "  tls_enable = false"},
    "nokia_srlinux": {
        "port": 57400,
        "tls": "  tls_enable = true\n  insecure_skip_verify = true",
    },
}
DEFAULT_GNMI_TRANSPORT = {"port": 57400, "tls": "  tls_enable = false"}


def _v(attr):
    return attr.get("value") if attr else None


def _node(rel):
    return rel.get("node") if rel else None


def _edges(rel):
    return [e["node"] for e in rel["edges"]] if rel else []


def _relevant_signals(data, device_name):
    """Signals watching a routing contract this device attaches, plus
    fleet-wide signals (watching nothing in particular)."""
    scoped, fleet = [], []
    for signal in _edges(data["IntentObservabilitySignal"]):
        watched = _node(signal.get("watches"))
        # the tenant hangs off the signal's own observability contract:
        # signal -> contract -> intent -> tenant
        own_contract = _node(signal.get("contract")) or {}
        own_intent = _node(own_contract.get("intent")) or {}
        tenant = _node(own_intent.get("tenant"))
        entry = {
            "name": _v(signal["name"]),
            "signal": _v(signal["signal"]),
            "direction": _v(signal.get("direction")),
            "frequency": _v(signal["frequency_seconds"]) or 30,
            "contract": _v(watched["name"]) if watched else None,
            "tenant": _v(tenant["name"]) if tenant else None,
        }
        if watched is None or "pe_devices" not in watched:
            fleet.append(entry)
            continue
        pe_names = {_v(d["name"]) for d in _edges(watched["pe_devices"])}
        if device_name in pe_names:
            scoped.append(entry)
    return scoped, fleet


class TelemetryCollectorTransform(InfrahubTransform):
    query = "telemetry_collector"

    async def transform(self, data):
        device = data["DcimDevice"]["edges"][0]["node"]
        name = _v(device["name"])
        platform = _node(device.get("platform")) or {}
        platform_name = _v(platform.get("name")) or "unknown"
        capabilities = {
            _v(c["name"]) for c in _edges(platform.get("capabilities"))
        }
        primary = _node(device.get("primary_address"))
        mgmt_ip = _v(primary["address"]).split("/")[0] if primary else None

        scoped, fleet = _relevant_signals(data, name)

        blocks = [
            f"# Telegraf inputs for {name} (platform: {platform_name})",
            f"# Compiled by InfraHub from ObservabilitySignal intent.",
            f"# Collector selection is capability-driven: {sorted(capabilities) or 'none'}",
            "",
        ]

        if scoped:
            if "gnmi" in capabilities:
                blocks.append(self._gnmi_block(name, platform_name, mgmt_ip, scoped))
            elif "ssh_cli" in capabilities:
                blocks.append(self._cli_scrape_block(name, platform_name, mgmt_ip, scoped))
            else:
                blocks.append(
                    f"# WARNING: {len(scoped)} contract-scoped signal(s) required but "
                    f"platform '{platform_name}' claims no telemetry capability.\n"
                    f"# The observability_capability check should have blocked this merge."
                )

        if mgmt_ip and any(f["signal"] == "device_reachability" for f in fleet):
            freq = min(
                f["frequency"] for f in fleet if f["signal"] == "device_reachability"
            )
            blocks.append(
                "\n".join([
                    "[[inputs.ping]]",
                    f'  urls = ["{mgmt_ip}"]',
                    f'  interval = "{freq}s"',
                    "  count = 3",
                    "  [inputs.ping.tags]",
                    f'    device = "{name}"',
                    '    signal = "device_reachability"',
                ])
            )

        return "\n\n".join(blocks) + "\n"

    @staticmethod
    def _gnmi_block(name, platform_name, mgmt_ip, signals):
        paths = {}
        for s in signals:
            for path in GNMI_PATHS.get(platform_name, {}).get(s["signal"], []):
                paths.setdefault(path, s)

        transport = GNMI_TRANSPORT.get(platform_name, DEFAULT_GNMI_TRANSPORT)
        lines = [
            "[[inputs.gnmi]]",
            f'  addresses = ["{mgmt_ip}:{transport["port"]}"]',
            '  username = "${GNMI_USER}"',
            '  password = "${GNMI_PASSWORD}"',
            f'  encoding = "json_ietf"',
            f'  redial = "10s"',
            transport["tls"],
            "  [inputs.gnmi.tags]",
            f'    device = "{name}"',
        ]
        contracts = sorted({s["contract"] for s in signals if s["contract"]})
        if contracts:
            lines.append(f'    contract = "{contracts[0]}"')
        for path, s in paths.items():
            lines += [
                "",
                "  [[inputs.gnmi.subscription]]",
                f'    name = "{s["signal"]}"',
                f'    path = "{path}"',
                '    subscription_mode = "sample"',
                f'    sample_interval = "{s["frequency"]}s"',
            ]
        return "\n".join(lines)

    @staticmethod
    def _cli_scrape_block(name, platform_name, mgmt_ip, signals):
        """No gNMI on this platform: compile an exec collector that SSHes in
        and emits influx line protocol. Same signals, different evidence
        pipeline - the intent did not change."""
        interval = min(s["frequency"] for s in signals)
        contracts = sorted({s["contract"] for s in signals if s["contract"]})
        contract_tag = contracts[0] if contracts else ""
        return "\n".join([
            "[[inputs.exec]]",
            "  commands = [",
            f'    "python3 /scripts/cli_bgp_telemetry.py --host {mgmt_ip} '
            f'--device {name} --platform {platform_name} --contract {contract_tag}",',
            "  ]",
            f'  interval = "{interval}s"',
            '  timeout = "30s"',
            '  data_format = "influx"',
        ])
