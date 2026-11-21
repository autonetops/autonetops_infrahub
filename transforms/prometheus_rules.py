"""Intent compiler: Prometheus alert rules.

Expectations are only real if something fires when they stop holding.
Each ObservabilityContract compiles into one Prometheus rule group; every
signal it carries becomes an alert rule built from its TYPED predicate:

    <catalog metric> <operator> <threshold | contract term>   for <for_seconds>

    bgp_session_state  eq 0                  for 60  -> session down
    bgp_route_count    gt ref:max_prefixes   for 0   -> over the contract limit
    device_reachability gt 50                for 120 -> OOB loss

Nothing here interprets text: the predicate is resolved by
``signal_catalog.resolve_predicate`` (the same function the
signal_predicate merge check runs), and the metric comes from the catalog.
Legacy free-text ``condition`` is parsed there only for unmigrated signals.

Rules are JOINS (signal catalog v1): collectors emit raw facts keyed by
device+neighbor; session-scoped expressions are joined against the
compiled ``intent_session_info`` / ``intent_threshold`` series
(intent_series.py). So labels are correct per neighbor even when a PE
serves N contracts, contract-referenced thresholds are data, and staleness
is per expected session, plus a guard for the expectation series
themselves being absent.

A signal that ``watches`` a routing contract alerts on that contract's
sessions and inherits its tenant; a device-scoped signal watching nothing
is fleet-wide (the OOB reachability ping).

Targeted artifact: one rules file per member of ``observability_contracts``.
"""

import pathlib
import sys

import yaml

from infrahub_sdk.transforms import InfrahubTransform

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import signal_catalog  # noqa: E402
from signal_catalog import INFO_METRIC, THRESHOLD_METRIC, selector  # noqa: E402

# Stable alert names (Alertmanager routes / silences key on them).
ALERT_SUFFIX = {
    "bgp_session_state": "bgp-session-down",
    "bgp_route_count": "route-count-exceeded",
    "policy_export_violation": "export-violation",
    "device_reachability": "device-unreachable",
    "interface_errors": "interface-errors",
}

SUMMARY = {
    "bgp_session_state": (
        "BGP session {{{{ $labels.device }}}} -> {{{{ $labels.neighbor }}}} "
        "for contract {contract} is down"),
    "bgp_route_count": (
        "Contract {contract} received more prefixes than authorized on "
        "{{{{ $labels.device }}}}"),
    "policy_export_violation": (
        "Routes exported on contract {contract} exceed the authorized set - "
        "possible leak of tenant {tenant} routes"),
    "device_reachability": (
        "Device {{{{ $labels.device }}}} stopped answering on the OOB "
        "management plane - the fleet-management-plane intent is violated"),
}


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


def _tenant_of(contract_node):
    intent = _node(contract_node.get("intent")) or {}
    return _node(intent.get("tenant"))


def _predicate(signal, sig):
    return signal_catalog.resolve_predicate(
        sig.id,
        operator=_v(signal.get("operator")),
        threshold=_v(signal.get("threshold")),
        threshold_ref=_v(signal.get("threshold_ref")),
        for_seconds=_v(signal.get("for_seconds")),
        condition=_v(signal.get("condition")),
        frequency_seconds=_v(signal.get("frequency_seconds")) or 30,
        name=_v(signal["name"]),
    )


def _session_expr(sig, pred, contract, watched):
    """Raw session metric joined to intent on the catalog keys, compared
    against a literal or the compiled contract threshold."""
    on = ", ".join(signal_catalog.SESSION_JOIN)
    lhs = (
        f"{sig.metric} * on({on}) group_left(contract, tenant, neighbor_device) "
        f"{INFO_METRIC}{selector({'contract': contract})}"
    )
    if pred.threshold_ref is None:
        return f"{lhs} {pred.promql_op} {pred.threshold}"
    # the series exists only if the contract defines the term - fail the
    # artifact rather than compile a comparison that can never fire
    afi_field = sig.threshold.get("ipv4")
    if _v(watched.get(afi_field)) is None:
        raise ValueError(
            f"signal on contract '{contract}' references {pred.threshold_ref} "
            f"but the contract defines no {afi_field}")
    rhs = selector({"contract": contract, "term": pred.threshold_ref, "afi": "ipv4"})
    return f"{lhs} {pred.promql_op} on(contract) group_left() {THRESHOLD_METRIC}{rhs}"


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
            pred = _predicate(signal, sig)
            freq = _v(signal["frequency_seconds"]) or 30

            watched = _node(signal.get("watches"))
            name = _v(watched["name"]) if watched else None
            tenant = (_tenant_of(watched) if watched else None) or own_tenant
            tenant_name = _v(tenant["name"]) if tenant else "infrastructure"

            labels = {
                "severity": _v(signal["severity"]),
                "contract": name or obs_name,
                "tenant": tenant_name,
                "signal": sig.id,
                "managed_by": "infrahub-intent",
            }
            stale_labels = {**labels, "severity": "sev3"}
            annotations = {
                "summary": SUMMARY.get(sig.id, f"Signal {sig.id} predicate violated")
                .format(contract=name or obs_name, tenant=tenant_name),
                "predicate": (
                    f"{sig.metric} {pred.promql_op} "
                    f"{pred.threshold if pred.threshold_ref is None else pred.threshold_ref}"
                    f" for {pred.for_seconds}s"
                ),
                "intent": _v(signal.get("condition")) or "",
            }

            if sig.scope == "device":
                if name:
                    # contract-scoped device signals need a device join the
                    # catalog does not define yet - say so, don't drop it
                    raise ValueError(
                        f"signal '{_v(signal['name'])}': device-scoped signal "
                        f"{sig.id} cannot watch contract '{name}' yet")
                rules.append({
                    "alert": f"{obs_name}-{ALERT_SUFFIX.get(sig.id, sig.id)}",
                    "expr": f"{sig.metric} {pred.promql_op} {pred.threshold}",
                    "for": f"{pred.for_seconds}s",
                    "labels": labels,
                    "annotations": annotations,
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

            if not name:
                raise ValueError(
                    f"signal '{_v(signal['name'])}': session-scoped signal "
                    f"{sig.id} must watch a contract")

            rules.append({
                "alert": f"{name}-{ALERT_SUFFIX.get(sig.id, sig.id)}",
                "expr": _session_expr(sig, pred, name, watched),
                "for": f"{pred.for_seconds}s",
                "labels": labels,
                "annotations": annotations,
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

        doc = {"groups": [{"name": f"intent-observability-{obs_name}", "rules": rules}]}
        return yaml.dump(
            doc, Dumper=_NoAliasDumper, sort_keys=False,
            default_flow_style=False, width=4096,
        )
