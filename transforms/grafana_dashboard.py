"""Intent compiler: per-tenant Grafana dashboard.

Observability intent is modeled up front, so the tenant dashboard is a
compiled artifact too - nobody hand-builds panels after an incident.
One dashboard per tenant: session health, route counts against the
contract's authorized maximum, leak evidence and OOB reachability.
"""

import json

from infrahub_sdk.transforms import InfrahubTransform


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
            policy = _node(node.get("policy")) or {}
            intent = _node(policy.get("intent")) or {}
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
                f'bgp_session_state_established{{contract="{cname}"}}',
                panel_type="stat", y=y, x=0,
            ))
            pid += 1
            panels.append(_panel(
                pid, f"{cname}: prefixes received (max {max_v4})",
                f'bgp_session_prefixes_received{{contract="{cname}"}}',
                threshold=max_v4, y=y, x=12,
            ))
            pid += 1
            y += 8

        # leak evidence: what is being exported toward denied zones is
        # watched on the peering contracts that could carry the leak
        panels.append(_panel(
            pid, "Export evidence toward peering/transit (leak watch)",
            'bgp_session_prefixes_sent{contract=~".*peer.*|.*transit.*"}',
            y=y, x=0,
        ))
        pid += 1
        panels.append(_panel(
            pid, "OOB reachability (packet loss %)",
            "ping_percent_packet_loss",
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
