"""Routing invariant: no-leak.

For a tenant contract carrying the ``no_leak`` invariant, the tenant's
routes must never be exported to any zone listed in the contract's
``export_deny_zones``. At the data layer that means: no other contract
whose peer zone is a denied zone may list this tenant in its
``export_tenants``.

This is the check that turns "customer C must never leak to internet
peers" from a review comment into a merge gate.

Targeted check: runs once per member of the ``intent_contracts`` group,
with the contract name passed as the ``contract`` parameter.
"""

from infrahub_sdk.checks import InfrahubCheck


class ContractNoLeakCheck(InfrahubCheck):
    query = "contract_no_leak"

    def validate(self, data):
        targets = data["target"]["edges"]
        if not targets:
            return
        contract = targets[0]["node"]

        invariants = {
            e["node"]["invariant_type"]["value"]
            for e in contract["invariants"]["edges"]
        }
        if "no_leak" not in invariants:
            return

        tenant = (contract.get("tenant") or {}).get("node")
        if tenant is None:
            self.log_error(
                message=f"Contract {contract['name']['value']} declares no_leak "
                        f"but has no tenant - invariant is unverifiable",
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
