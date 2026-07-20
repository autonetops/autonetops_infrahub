"""Intent compiler: Prometheus alert rules.

Expectations are only real if something fires when they stop holding.
Each ObservabilityContract compiles into one Prometheus rule group: every
signal it carries becomes alert rules, and the signal's small condition
DSL maps onto PromQL:

    "down_for > 60s"        -> expr == 0, for: 60s
    "count > max_prefixes"  -> threshold taken from the WATCHED contract
    "count > 25"            -> literal threshold
    "loss > 50%"            -> ping packet loss threshold (fleet signals)

A signal that ``watches`` a routing contract alerts on that contract's
metrics and inherits its tenant; a signal watching nothing is fleet-wide
(the OOB reachability ping). Severity comes straight from the intent
(sev1..sev4) and lands as a label the alertmanager routes on. The rules
also encode the freshness expectation: stale evidence is itself an
alert, because unobserved compliance is not compliance.

Targeted artifact: one rules file per member of ``observability_contracts``.
"""

import re

import yaml

from infrahub_sdk.transforms import InfrahubTransform


def _v(attr):
    return attr.get("value") if attr else None


def _node(rel):
    return rel.get("node") if rel else None


def _edges(rel):
    return [e["node"] for e in rel["edges"]] if rel else []


def _parse_duration(condition):
    m = re.search(r"down_for\s*>\s*(\d+)s", condition or "")
    return f"{m.group(1)}s" if m else "60s"


def _parse_threshold(condition, contract_max):
    if condition and "max_prefixes" in condition:
        return contract_max
    m = re.search(r"count\s*>\s*(\d+)", condition or "")
    return int(m.group(1)) if m else None


def _parse_loss(condition):
    m = re.search(r"loss\s*>\s*(\d+)\s*%", condition or "")
    return int(m.group(1)) if m else 50


def _tenant_of(contract_node):
    intent = _node(contract_node.get("intent")) or {}
    return _node(intent.get("tenant"))


class PrometheusRulesTransform(InfrahubTransform):
    query = "prometheus_rules"

    async def transform(self, data):
        obs = data["IntentObservabilityContract"]["edges"][0]["node"]
        obs_name = _v(obs["name"])
        own_tenant = _tenant_of(obs)

        rules = []
        for signal in _edges(obs.get("signals")):
            sig = _v(signal["signal"])
            severity = _v(signal["severity"])
            condition = _v(signal["condition"])
            freq = _v(signal["frequency_seconds"]) or 30

            watched = _node(signal.get("watches"))
            if watched:
                name = _v(watched["name"])
                max_v4 = _v(watched.get("max_prefixes_ipv4"))
                tenant = _tenant_of(watched) or own_tenant
            else:
                name = None
                max_v4 = None
                tenant = own_tenant
            tenant_name = _v(tenant["name"]) if tenant else "infrastructure"

            labels = {
                "severity": severity,
                "contract": name or obs_name,
                "tenant": tenant_name,
                "managed_by": "infrahub-intent",
            }

            if sig == "bgp_session_state" and name:
                rules.append({
                    "alert": f"{name}-bgp-session-down",
                    "expr": (
                        f'bgp_session_state_established{{contract="{name}"}} == 0'
                    ),
                    "for": _parse_duration(condition),
                    "labels": labels,
                    "annotations": {
                        "summary": f"BGP session for contract {name} is down",
                        "intent": condition or "",
                    },
                })
            elif sig == "bgp_route_count" and name:
                threshold = _parse_threshold(condition, max_v4)
                if threshold is not None:
                    rules.append({
                        "alert": f"{name}-route-count-exceeded",
                        "expr": (
                            f'bgp_session_prefixes_received{{contract="{name}"}} '
                            f"> {threshold}"
                        ),
                        "for": "0s",
                        "labels": labels,
                        "annotations": {
                            "summary": (
                                f"Contract {name} received more prefixes than "
                                f"authorized ({threshold})"
                            ),
                            "intent": condition or "",
                        },
                    })
            elif sig == "policy_export_violation" and name:
                threshold = _parse_threshold(condition, max_v4) or 0
                rules.append({
                    "alert": f"{name}-export-violation",
                    "expr": (
                        f'bgp_session_prefixes_sent{{contract="{name}"}} '
                        f"> {threshold}"
                    ),
                    "for": "0s",
                    "labels": labels,
                    "annotations": {
                        "summary": (
                            f"Routes exported on contract {name} exceed the "
                            f"authorized set - possible leak of tenant "
                            f"{tenant_name} routes"
                        ),
                        "intent": condition or "",
                    },
                })
            elif sig == "device_reachability":
                threshold = _parse_loss(condition)
                rules.append({
                    "alert": f"{obs_name}-device-unreachable",
                    "expr": f"ping_percent_packet_loss > {threshold}",
                    "for": f"{freq * 2}s",
                    "labels": labels,
                    "annotations": {
                        "summary": (
                            "A device stopped answering on the OOB management "
                            "plane - the fleet-management-plane intent is violated"
                        ),
                        "intent": condition or "",
                    },
                })
                # ping freshness has its own shape
                rules.append({
                    "alert": f"{obs_name}-{sig}-telemetry-stale",
                    "expr": "absent(ping_percent_packet_loss)",
                    "for": f"{freq * 2}s",
                    "labels": {**labels, "severity": "sev3"},
                    "annotations": {
                        "summary": (
                            f"No fresh evidence for signal {sig} - "
                            f"compliance cannot be claimed"
                        ),
                    },
                })
                continue

            if not name:
                continue

            # freshness: two missed collection cycles means the evidence is
            # stale and the intent can no longer be claimed as satisfied
            rules.append({
                "alert": f"{name}-{sig}-telemetry-stale",
                "expr": f'absent(bgp_session_state_established{{contract="{name}"}})'
                if sig == "bgp_session_state"
                else f'absent(bgp_session_prefixes_received{{contract="{name}"}})',
                "for": f"{freq * 2}s",
                "labels": {**labels, "severity": "sev3"},
                "annotations": {
                    "summary": (
                        f"No fresh evidence for signal {sig} on contract {name} - "
                        f"compliance cannot be claimed"
                    ),
                },
            })

        doc = {
            "groups": [
                {
                    "name": f"intent-observability-{obs_name}",
                    "rules": rules,
                }
            ]
        }
        return yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)
