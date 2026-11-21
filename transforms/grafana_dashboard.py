"""Intent compiler: per-tenant Grafana dashboard.

Observability intent is modeled up front, so the tenant dashboard is a
compiled artifact too - nobody hand-builds panels after an incident.
One dashboard per tenant: session health, route counts against the
contract's authorized maximum, leak evidence and OOB reachability.
"""

import json
import pathlib
import sys

from infrahub_sdk.transforms import InfrahubTransform

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import signal_catalog  # noqa: E402
from signal_catalog import INFO_METRIC, selector  # noqa: E402


def _joined(signal_id, match):
    """Raw metric with intent labels joined in (collectors carry no
    contract labels - see intent_series.py)."""
    metric = signal_catalog.spec(signal_id).metric
    on = ", ".join(signal_catalog.SESSION_JOIN)
    return (
        f"{metric} * on({on}) group_left(contract, tenant, role) "
        f"{INFO_METRIC}{match}"
    )


def _v(attr):
    return attr.get("value") if attr else None


def _node(rel):
    return rel.get("node") if rel else None


def _edges(rel):
    return [e["node"] for e in rel["edges"]] if rel else []


def _panel(panel_id, title, expr, panel_type="timeseries", threshold=None, y=0, x=0):
    panel = {
        "id": panel_id,
        "title": title,
        "type": panel_type,
        "datasource": {"type": "prometheus", "uid": "prometheus"},
        "gridPos": {"h": 8, "w": 12, "x": x, "y": y},
        "targets": [
            {
                "expr": expr,
                "refId": "A",
                "legendFormat": "{{device}} {{neighbor}}",
            }
        ],
        "fieldConfig": {"defaults": {}, "overrides": []},
    }
    if threshold is not None:
        panel["fieldConfig"]["defaults"]["thresholds"] = {
            "mode": "absolute",
            "steps": [
                {"color": "green", "value": None},
                {"color": "red", "value": threshold},
            ],
        }
        panel["fieldConfig"]["defaults"]["custom"] = {
            "thresholdsStyle": {"mode": "line"}
        }
    return panel


class GrafanaDashboardTransform(InfrahubTransform):
    query = "grafana_dashboard"

    async def transform(self, data):
        tenant = data["IntentTenant"]["edges"][0]["node"]
        tenant_name = _v(tenant["name"])

        def _contract_tenant(node):
            intent = _node(node.get("intent")) or {}
            return _node(intent.get("tenant"))

        contracts = [
            c for c in _edges(data["IntentRoutingContract"])
            if (t := _contract_tenant(c)) and _v(t["name"]) == tenant_name
        ]

        panels = []
        pid = 1
        y = 0
        for contract in contracts:
            cname = _v(contract["name"])
            max_v4 = _v(contract.get("max_prefixes_ipv4"))
            panels.append(_panel(
                pid, f"{cname}: BGP session state",
                _joined("bgp_session_state", selector({"contract": cname})),
                panel_type="stat", y=y, x=0,
            ))
            pid += 1
            panels.append(_panel(
                pid, f"{cname}: prefixes received (max {max_v4})",
                _joined("bgp_route_count", selector({"contract": cname})),
                threshold=max_v4, y=y, x=12,
            ))
            pid += 1
            y += 8

        # leak evidence: what is being exported toward denied zones is
        # watched on the peering contracts that could carry the leak
        panels.append(_panel(
            pid, "Export evidence toward peering/transit (leak watch)",
            # selected by contract ROLE (compiled into the info series),
            # not by guessing from contract names
            _joined("policy_export_violation",
                    '{role=~"internet_peering|transit"}'),
            y=y, x=0,
        ))
        pid += 1
        panels.append(_panel(
            pid, "OOB reachability (packet loss %)",
            signal_catalog.spec("device_reachability").metric,
            threshold=50, y=y, x=12,
        ))

        dashboard = {
            "uid": f"intent-{tenant_name}",
            "title": f"Tenant Intent: {tenant_name}",
            "description": (
                f"Compiled from InfraHub intent - env: {_v(tenant['environment'])}, "
                f"owner: {_v(tenant['owner'])}. Do not hand-edit."
            ),
            "tags": ["infrahub", "intent", tenant_name],
            "timezone": "browser",
            "refresh": "30s",
            "time": {"from": "now-1h", "to": "now"},
            "panels": panels,
            "schemaVersion": 39,
        }
        return json.dumps(dashboard, indent=2)
