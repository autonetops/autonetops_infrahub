"""Routing invariant: prefix authorization.

Every prefix a contract authorizes the remote side to advertise must fall
inside the owning tenant's segments (the prefixes assigned to the
tenant's VRFs). A contract cannot authorize address space the tenant does
not own - that is how hijacks and fat-finger announcements get through.

Targeted check: runs once per member of the ``routing_contracts`` group.
The tenant hangs off the contract's intent:
contract -> intent -> tenant.
"""

import ipaddress

from infrahub_sdk.checks import InfrahubCheck


def _tenant(node):
    intent = (node.get("intent") or {}).get("node") or {}
    return (intent.get("tenant") or {}).get("node")


class ContractPrefixAuthorizationCheck(InfrahubCheck):
    query = "contract_prefix_authorization"

    def validate(self, data):
        contracts = data["IntentRoutingContract"]["edges"]
        if not contracts:
            return
        contract = contracts[0]["node"]
        name = contract["name"]["value"]

        tenant = _tenant(contract)
        if tenant is None:
            # Infrastructure contracts (peering/transit) own no tenant space.
            return

        vrf_names = {
            e["node"]["name"]["value"] for e in tenant["vrfs"]["edges"]
        }
        tenant_networks = []
        for edge in data["IpamPrefix"]["edges"]:
            node = edge["node"]
            vrf = (node.get("vrf") or {}).get("node")
            if vrf and vrf["name"]["value"] in vrf_names:
                tenant_networks.append(ipaddress.ip_network(node["prefix"]["value"]))

        if not tenant_networks:
            self.log_error(
                message=f"Contract {name}: tenant '{tenant['name']['value']}' has no "
                        f"prefixes in its VRFs - nothing can be authorized",
            )
            return

        for edge in contract["allowed_prefixes"]["edges"]:
            allowed = ipaddress.ip_network(edge["node"]["prefix"]["value"])
            authorized = any(
                allowed.version == net.version and allowed.subnet_of(net)
                for net in tenant_networks
            )
            if not authorized:
                self.log_error(
                    message=f"Invariant violated (prefix_authorization): contract "
                            f"{name} authorizes {allowed}, which is outside every "
                            f"segment owned by tenant '{tenant['name']['value']}'",
                )
