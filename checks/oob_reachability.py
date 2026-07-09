"""Reliability invariant: every device must be reachable from OOB management.

Reachable means: the device has at least one interface with role
``management`` whose cable lands on a member of the ``oob_switches``
CoreStandardGroup. The traversal runs entirely on the Proposed Change
branch data returned by the ``oob_reachability`` query - no live network
access, this validates the *modeled* OOB path.
"""

from infrahub_sdk.checks import InfrahubCheck


class OOBReachabilityCheck(InfrahubCheck):
    query = "oob_reachability"

    def validate(self, data):
        groups = data["CoreStandardGroup"]["edges"]
        oob_ids = {
            m["node"]["id"]
            for g in groups
            for m in g["node"]["members"]["edges"]
        }
        if not oob_ids:
            self.log_error(
                message="Invariant violated: group 'oob_switches' is empty - "
                        "OOB reachability intent cannot be satisfied",
            )
            return

        for edge in data["DcimDevice"]["edges"]:
            device = edge["node"]
            if device["id"] in oob_ids:
                continue

            if not self._has_oob_path(device, oob_ids):
                self.log_error(
                    message=f"Invariant violated: {device['name']['value']} has no "
                            f"management interface cabled to an OOB switch",
                    object_id=device["id"],
                    object_type="DcimDevice",
                )

    @staticmethod
    def _has_oob_path(device, oob_ids):
        for iedge in device["interfaces"]["edges"]:
            iface = iedge["node"]
            role = (iface.get("role") or {}).get("value")
            if role != "management":
                continue
            connector = (iface.get("connector") or {}).get("node")
            if not connector:
                continue
            for endpoint in connector["connected_endpoints"]["edges"]:
                remote_device = ((endpoint["node"] or {}).get("device") or {}).get("node")
                if remote_device and remote_device["id"] in oob_ids:
                    return True
        return False
