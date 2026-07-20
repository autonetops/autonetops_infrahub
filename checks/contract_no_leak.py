"""No-leak invariant - merge-time projection.

When a routing contract's intent is guaranteed by a no-leak invariant
(``IntentNoLeakInvariant``), the owning tenant's routes must never be
exported to any zone listed in the contract's ``export_deny_zones``. At
the data layer that means: no other contract whose peer zone is a denied
zone may list this tenant in its ``export_tenants``.

This is the projection that turns "customer C must never leak to internet
peers" from a review comment into a merge gate (the invariant's runtime
surface is its compiled expectation: forbidden exports absent).

Targeted check: runs once per member of the ``routing_contracts`` group,
with the contract name passed as the ``contract`` parameter. The tenant
and the invariant both hang off the contract's intent:
contract -> intent -> invariants, contract -> intent -> tenant.
"""

from infrahub_sdk.checks import InfrahubCheck


def _intent(node):
    return (node.get("intent") or {}).get("node") or {}


def _invariant_kinds(node):
    return {
        e["node"]["__typename"]
        for e in (_intent(node).get("invariants") or {}).get("edges", [])
    }


def _tenant(node):
    return (_intent(node).get("tenant") or {}).get("node")


class ContractNoLeakCheck(InfrahubCheck):
    query = "contract_no_leak"

    def validate(self, data):
        targets = data["target"]["edges"]
        if not targets:
            return
        contract = targets[0]["node"]

        if "IntentNoLeakInvariant" not in _invariant_kinds(contract):
            return

        tenant = _tenant(contract)
        if tenant is None:
            self.log_error(
                message=f"Contract {contract['name']['value']} declares no_leak "
                        f"but its intent has no tenant - invariant is unverifiable",
            )
            return
        tenant_name = tenant["name"]["value"]

        deny_zones = {
            e["node"]["name"]["value"]
            for e in contract["export_deny_zones"]["edges"]
        }
        if not deny_zones:
            self.log_error(
                message=f"Contract {contract['name']['value']} declares no_leak "
                        f"but export_deny_zones is empty - invariant is vacuous",
            )
            return

        for edge in data["all_contracts"]["edges"]:
            other = edge["node"]
            zone = (other.get("zone") or {}).get("node")
            if not zone or zone["name"]["value"] not in deny_zones:
                continue
            exported = {
                e["node"]["name"]["value"]
                for e in other["export_tenants"]["edges"]
            }
            if tenant_name in exported:
                self.log_error(
                    message=f"Invariant violated (no_leak): tenant '{tenant_name}' "
                            f"is listed in export_tenants of contract "
                            f"'{other['name']['value']}' toward denied zone "
                            f"'{zone['name']['value']}'",
                )
