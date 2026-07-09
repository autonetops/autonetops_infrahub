"""Intent compiler: Prometheus alert rules.

Expectations are only real if something fires when they stop holding.
Each contract's ObservabilitySignals compile into a Prometheus rule
group; the signal's small condition DSL maps onto PromQL:

    "down_for > 60s"        -> expr == 0, for: 60s
    "count > max_prefixes"  -> threshold taken from the contract
    "count > 25"            -> literal threshold

Severity comes straight from the intent (sev1..sev4) and lands as a
label the alertmanager routes on. The rules also encode the freshness
expectation: stale evidence is itself an alert, because unobserved
compliance is not compliance.
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


class PrometheusRulesTransform(InfrahubTransform):
    query = "prometheus_rules"

    async def transform(self, data):
        contract = data["IntentRoutingContract"]["edges"][0]["node"]
        name = _v(contract["name"])
        tenant = _node(contract.get("tenant"))
        tenant_name = _v(tenant["name"]) if tenant else "infrastructure"
        max_v4 = _v(contract.get("max_prefixes_ipv4"))

        rules = []
        for signal in _edges(data["IntentObservabilitySignal"]):
            scoped = _node(signal.get("contract"))
            if not scoped or _v(scoped["name"]) != name:
                continue

            sig = _v(signal["signal"])
            severity = _v(signal["severity"])
            condition = _v(signal["condition"])
            freq = _v(signal["frequency_seconds"]) or 30
            labels = {
                "severity": severity,
                "contract": name,
                "tenant": tenant_name,
                "managed_by": "infrahub-intent",
            }

            if sig == "bgp_session_state":
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
            elif sig == "bgp_route_count":
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
            elif sig == "policy_export_violation":
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
                    "name": f"intent-contract-{name}",
                    "rules": rules,
                }
            ]
        }
        return yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)
