"""Intent compiler: ContractExpectations.

The compiler does not stop at configuration. For every routing contract
it also emits a machine-readable statement of what the network should
look like *if the intent is being met* - the unit tests of runtime
behavior.

Notice what this artifact encodes:
  - control-plane facts (session established, AFI/SAFI, uptime)
  - policy facts as ranges/invariants, never exact values
    ("route count <= 200" is stable; "== 173" collapses on the next
    legitimate prefix)
  - forbidden behavior (no export to denied zones)
  - telemetry freshness - if you can't observe it, you can't claim
    compliance - including HOW each device is observed (gnmi vs
    cli_scrape), derived from platform capabilities.

A validator (execution layer, out of scope here) consumes this file and
compares it against Prometheus/telegraf evidence continuously.
"""

import yaml

from infrahub_sdk.transforms import InfrahubTransform

MIN_UPTIME_SECONDS = 300


def _v(attr):
    return attr.get("value") if attr else None


def _node(rel):
    return rel.get("node") if rel else None


def _edges(rel):
    return [e["node"] for e in rel["edges"]] if rel else []


def _iface_ips(iface):
    ips = iface.get("ip_addresses")
    return [_v(ip["address"]) for ip in _edges(ips)] if ips else []


def _collector_for(capabilities):
    """Capability-driven collector selection - the same switchboard the
    telemetry compiler uses, recorded here as an expectation."""
    if "gnmi" in capabilities:
        return "gnmi"
    if "snmp" in capabilities:
        return "snmp"
    if "ssh_cli" in capabilities:
        return "cli_scrape"
    return "unobservable"


def _derive_sessions(contract_node):
    """Same rule as the config renderer: sessions are a consequence of the
    contract plus the modeled cabling, never modeled by hand."""
    ce_names = {_v(d["name"]) for d in _edges(contract_node.get("ce_devices"))}
    sessions = []
    for pe in _edges(contract_node.get("pe_devices")):
        platform = _node(pe.get("platform")) or {}
        capabilities = {
            _v(c["name"]) for c in _edges(platform.get("capabilities"))
        }
        for iface in _edges(pe.get("interfaces")):
            connector = _node(iface.get("connector"))
            if not connector:
                continue
            for endpoint in _edges(connector["connected_endpoints"]):
                remote_dev = _node(endpoint.get("device"))
                if not remote_dev or _v(remote_dev["name"]) not in ce_names:
                    continue
                remote_ips = _iface_ips(endpoint)
                if not remote_ips:
                    continue
                sessions.append({
                    "device": _v(pe["name"]),
                    "interface": _v(iface["name"]),
                    "neighbor_ip": remote_ips[0].split("/")[0],
                    "neighbor_device": _v(remote_dev["name"]),
                    "collector": _collector_for(capabilities),
                })
    return sessions


class ContractExpectationsTransform(InfrahubTransform):
    query = "contract_expectations"

    async def transform(self, data):
        contract = data["IntentRoutingContract"]["edges"][0]["node"]
        name = _v(contract["name"])
        # the hierarchy above the contract:
        # contract -> policy -> intent -> (realm, tenant)
        policy = _node(contract.get("policy")) or {}
        intent = _node(policy.get("intent")) or {}
        realm = _node(intent.get("realm")) or {}
        tenant = _node(intent.get("tenant"))
        invariants = _edges(policy.get("invariants"))

        sessions = []
        for s in _derive_sessions(contract):
            sessions.append({
                "device": s["device"],
                "neighborIp": s["neighbor_ip"],
                "neighborDevice": s["neighbor_device"],
                "peerAsn": _v(contract["peer_asn"]),
                "afiSafis": _v(contract["afi_safis"]) or [],
                "observedVia": s["collector"],
                "mustBe": {
                    "sessionState": "established",
                    "minUptimeSeconds": MIN_UPTIME_SECONDS,
                },
            })

        max_prefixes = {}
        if _v(contract.get("max_prefixes_ipv4")):
            max_prefixes["ipv4"] = _v(contract["max_prefixes_ipv4"])
        if _v(contract.get("max_prefixes_ipv6")):
            max_prefixes["ipv6"] = _v(contract["max_prefixes_ipv6"])

        must_include = (
            (_v(contract.get("require_communities")) or [])
            + (_v(contract.get("attach_communities")) or [])
        )
        deny_zones = [
            _v(z["name"]) for z in _edges(contract.get("export_deny_zones"))
        ]

        forbidden_actions = []
        for inv in invariants:
            itype = _v(inv["invariant_type"])
            if itype == "no_leak" and tenant:
                forbidden_actions.append(
                    f"export_prefixes_from_tenant({_v(tenant['name'])})"
                )
            elif itype == "no_default_origination":
                forbidden_actions.append("originate_default_route_to_peer")
            elif itype == "no_reorigination":
                forbidden_actions.append("reoriginate_learned_routes")

        telemetry = []
        alerting = []
        for signal in _edges(data["IntentObservabilitySignal"]):
            watched = _node(signal.get("watches"))
            if not watched or _v(watched["name"]) != name:
                continue
            freq = _v(signal["frequency_seconds"]) or 30
            telemetry.append({
                "signal": _v(signal["signal"]),
                "direction": _v(signal.get("direction")),
                # stale evidence is no evidence: two missed cycles = drift
                "freshnessSeconds": freq * 2,
            })
            alerting.append({
                "signal": _v(signal["signal"]),
                "severity": _v(signal["severity"]),
                "condition": _v(signal["condition"]),
            })

        expectations = {
            "apiVersion": "expectations.networking.autonetops/v1",
            "kind": "ContractExpectations",
            "metadata": {
                "name": name,
                "realm": _v(realm.get("name")),
                "intent": _v(intent.get("name")),
                "statement": _v(intent.get("statement")),
                "policy": _v(policy.get("name")),
                "enforcement": _v(policy.get("enforcement")),
                "tenant": _v(tenant["name"]) if tenant else None,
                "zone": _v((_node(contract.get("zone")) or {}).get("name")),
            },
            "spec": {
                "bgp": {
                    "sessions": sessions,
                    "routePolicy": {
                        "inbound": {
                            "maxPrefixes": max_prefixes or None,
                            "allowedPrefixes": [
                                _v(p["prefix"])
                                for p in _edges(contract.get("allowed_prefixes"))
                            ],
                            "mustIncludeCommunities": must_include,
                        },
                        "outbound": {
                            "forbiddenZones": deny_zones,
                            "forbiddenActions": forbidden_actions,
                        },
                    },
                },
                "telemetry": {"required": telemetry},
                "alerting": alerting,
            },
        }
        return yaml.safe_dump(expectations, sort_keys=False, default_flow_style=False)
