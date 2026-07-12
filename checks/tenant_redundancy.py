"""Reliability invariant: attachment redundancy.

For every tenant that declares redundancy (via a ReliabilityContract
and/or a ReachabilityContract with require_redundancy), each of its
customer_edge routing contracts must attach to at least
``min_pe_attachments`` distinct PE devices - and, when required, those
PEs must sit in distinct failure domains (different locations).

This is the guardrail that stops the design from quietly degrading as
exceptions accumulate. Tenants hang off each contract's policy:
contract -> policy -> intent -> tenant.
"""

from infrahub_sdk.checks import InfrahubCheck

DEFAULT_MIN_ATTACHMENTS = 2


def _tenant(node):
    policy = (node.get("policy") or {}).get("node") or {}
    intent = (policy.get("intent") or {}).get("node") or {}
    return (intent.get("tenant") or {}).get("node")


class TenantRedundancyCheck(InfrahubCheck):
    query = "tenant_redundancy"

    def validate(self, data):
        requirements = {}

        for edge in data["IntentReliabilityContract"]["edges"]:
            node = edge["node"]
            tenant = _tenant(node)
            if not tenant:
                continue
            requirements[tenant["name"]["value"]] = {
                "min": node["min_pe_attachments"]["value"] or DEFAULT_MIN_ATTACHMENTS,
                "distinct_fd": bool(node["require_distinct_failure_domains"]["value"]),
            }

        for edge in data["IntentReachabilityContract"]["edges"]:
            node = edge["node"]
            if not node["require_redundancy"]["value"]:
                continue
            tenant = _tenant(node)
            if not tenant:
                continue
            requirements.setdefault(
                tenant["name"]["value"],
                {"min": DEFAULT_MIN_ATTACHMENTS, "distinct_fd": False},
            )

        for edge in data["IntentRoutingContract"]["edges"]:
            contract = edge["node"]
            tenant = _tenant(contract)
            if not tenant or tenant["name"]["value"] not in requirements:
                continue
            req = requirements[tenant["name"]["value"]]

            pes = [e["node"] for e in contract["pe_devices"]["edges"]]
            pe_names = {p["name"]["value"] for p in pes}
            if len(pe_names) < req["min"]:
                self.log_error(
                    message=f"Invariant violated (redundancy): contract "
                            f"{contract['name']['value']} attaches to "
                            f"{len(pe_names)} PE(s), tenant "
                            f"'{tenant['name']['value']}' requires {req['min']}",
                )
                continue

            if req["distinct_fd"]:
                locations = {
                    (p.get("location") or {}).get("node", {}).get("display_label")
                    for p in pes
                }
                locations.discard(None)
                if len(locations) < req["min"]:
                    self.log_error(
                        message=f"Invariant violated (failure-domain separation): "
                                f"contract {contract['name']['value']} PEs "
                                f"{sorted(pe_names)} share failure domains "
                                f"({sorted(locations)})",
                    )
