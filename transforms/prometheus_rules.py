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

Rules are JOINS (signal catalog v1): collectors emit raw facts keyed by
device+neighbor, and every contract-scoped expression is joined against
the compiled ``intent_session_info`` / ``intent_threshold`` series
(intent_series.py) on the catalog's join keys. So:

  - labels are correct per neighbor even when a PE serves N contracts
  - thresholds are DATA (``intent_threshold``) - a max_prefixes change
    recompiles a series, not this file
  - staleness is per expected session ("expected but not observed"),
    plus a guard for the expectation series themselves being absent

Targeted artifact: one rules file per member of ``observability_contracts``.
"""

import pathlib
import re
import sys

import yaml

from infrahub_sdk.transforms import InfrahubTransform

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import signal_catalog  # noqa: E402
from signal_catalog import INFO_METRIC, THRESHOLD_METRIC, selector  # noqa: E402


def _v(attr):
    return attr.get("value") if attr else None


def _node(rel):
    return rel.get("node") if rel else None


def _edges(rel):
    return [e["node"] for e in rel["edges"]] if rel else []


class _NoAliasDumper(yaml.SafeDumper):
    """Shared label dicts must render inline, not as &id001 anchors."""

    def ignore_aliases(self, data):
        return True


def _parse_duration(condition):
    m = re.search(r"down_for\s*>\s*(\d+)s", condition or "")
    return f"{m.group(1)}s" if m else "60s"


def _parse_threshold(condition):
    """-> ("contract", None) when the condition defers to the contract's
    max_prefixes term, ("literal", n) for a literal bound, (None, None)."""
    if condition and "max_prefixes" in condition:
        return "contract", None
    m = re.search(r"count\s*>\s*(\d+)", condition or "")
    return ("literal", int(m.group(1))) if m else (None, None)


def _joined(metric, contract):
    """Raw session metric with intent labels joined on (device, neighbor)."""
    on = ", ".join(signal_catalog.SESSION_JOIN)
    return (
        f"{metric} * on({on}) group_left(contract, tenant, neighbor_device) "
        f"{INFO_METRIC}{selector({'contract': contract})}"
    )


def _bound(joined, condition, contract, max_v4, signal_name):
    kind, literal = _parse_threshold(condition)
    if kind == "literal":
        return f"{joined} > {literal}"
    if kind == "contract":
        if max_v4 is None:
            # a bound that resolves to nothing is an unwatched contract -
            # fail the artifact instead of compiling a rule that never fires
            raise ValueError(
                f"signal '{signal_name}' compares against max_prefixes but "
                f"contract '{contract}' defines no max_prefixes_ipv4"
            )
        thr = selector({"contract": contract, "term": "max_prefixes", "afi": "ipv4"})
        return f"{joined} > on(contract) group_left() {THRESHOLD_METRIC}{thr}"
    return None


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
        guarded = set()  # contracts whose expectation series we already guard
        for signal in _edges(obs.get("signals")):
            sig = signal_catalog.spec(_v(signal["signal"]))
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
                "signal": sig.id,
                "managed_by": "infrahub-intent",
            }
            stale_labels = {**labels, "severity": "sev3"}

            if sig.id == "device_reachability":
                rules.append({
                    "alert": f"{obs_name}-device-unreachable",
                    "expr": f"{sig.metric} > {_parse_loss(condition)}",
                    "for": f"{freq * 2}s",
                    "labels": labels,
                    "annotations": {
                        "summary": (
                            "Device {{ $labels.device }} stopped answering on the "
                            "OOB management plane - the fleet-management-plane "
                            "intent is violated"
                        ),
                        "intent": condition or "",
                    },
                })
                rules.append({
                    "alert": f"{obs_name}-{sig.id}-telemetry-stale",
                    "expr": f"absent({sig.metric})",
                    "for": f"{freq * 2}s",
                    "labels": stale_labels,
                    "annotations": {
                        "summary": (
                            f"No fresh evidence for signal {sig.id} - "
                            f"compliance cannot be claimed"
                        ),
                    },
                })
                continue

            if sig.scope != "session" or not name:
                # device-scoped contract signals (interface_errors) have no
                # predicate grammar yet - owed with typed predicates (step 2)
                continue

            joined = _joined(sig.metric, name)
            if sig.id == "bgp_session_state":
                rules.append({
                    "alert": f"{name}-bgp-session-down",
                    "expr": f"{joined} == 0",
                    "for": _parse_duration(condition),
                    "labels": labels,
                    "annotations": {
                        "summary": (
                            f"BGP session {{{{ $labels.device }}}} -> "
                            f"{{{{ $labels.neighbor }}}} for contract {name} is down"
                        ),
                        "intent": condition or "",
                    },
                })
            elif sig.id in ("bgp_route_count", "policy_export_violation"):
                expr = _bound(joined, condition, name, max_v4, _v(signal["name"]))
                if expr is None:
                    raise ValueError(
                        f"signal '{_v(signal['name'])}': condition "
                        f"'{condition}' has no recognizable bound"
                    )
                received = sig.id == "bgp_route_count"
                rules.append({
                    "alert": (
                        f"{name}-route-count-exceeded" if received
                        else f"{name}-export-violation"
                    ),
                    "expr": expr,
                    "for": "0s",
                    "labels": labels,
                    "annotations": {
                        "summary": (
                            f"Contract {name} received more prefixes than "
                            f"authorized on {{{{ $labels.device }}}}"
                            if received else
                            f"Routes exported on contract {name} exceed the "
                            f"authorized set - possible leak of tenant "
                            f"{tenant_name} routes"
                        ),
                        "intent": condition or "",
                    },
                })

            # freshness, per expected session: two missed collection cycles
            # and the intent can no longer be claimed as satisfied
            on = ", ".join(signal_catalog.SESSION_JOIN)
            rules.append({
                "alert": f"{name}-{sig.id}-telemetry-stale",
                "expr": (
                    f"{INFO_METRIC}{selector({'contract': name})} "
                    f"unless on({on}) {sig.metric}"
                ),
                "for": f"{freq * 2}s",
                "labels": stale_labels,
                "annotations": {
                    "summary": (
                        f"Session {{{{ $labels.device }}}} -> "
                        f"{{{{ $labels.neighbor }}}} is expected by contract "
                        f"{name} but signal {sig.id} is not observed - "
                        f"compliance cannot be claimed"
                    ),
                },
            })
            if name not in guarded:
                guarded.add(name)
                rules.append({
                    "alert": f"{name}-expectations-missing",
                    "expr": f"absent({INFO_METRIC}{selector({'contract': name})})",
                    "for": "5m",
                    "labels": stale_labels,
                    "annotations": {
                        "summary": (
                            f"No compiled expectation series for contract {name} "
                            f"(intent-series artifact not delivered) - every "
                            f"alert for it is blind"
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
        return yaml.dump(
            doc, Dumper=_NoAliasDumper, sort_keys=False,
            default_flow_style=False, width=4096,
        )
