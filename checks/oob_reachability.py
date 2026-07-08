# checks/oob_reachability.py
from infrahub_sdk.checks import InfrahubCheck

class OOBReachabilityCheck(InfrahubCheck):
    query = "oob_reachability"   # nome da query no .infrahub.yml

    def validate(self, data):
        groups = data["CoreStandardGroup"]["edges"]
        oob_ids = [
            m["node"]["id"]
            for g in groups
            for m in g["node"]["members"]["edges"]
        ]
        devices = [d["node"] for d in data["DcimDevice"]["edges"]]

        for device in devices:
            if device["id"] in oob_ids:
                continue
            # traversal na branch do proposed change (self.branch)
            reachable = any(
                self.client.path_exists(          # verifique assinatura no SDK 1.20+
                    source_id=oob_id,
                    destination_id=device["id"],
                    branch=self.branch,
                )
                for oob_id in oob_ids
            )
            if not reachable:
                self.log_error(
                    message=f"Invariant violated: {device['display_label']} "
                            f"unreachable from OOB management",
                    object_id=device["id"],
                    object_type="DcimDevice",
                )