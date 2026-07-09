"""Observability invariant: if you can't observe it, you can't claim compliance.

Every required signal scoped to a contract must be collectable on every
PE the contract attaches to: the PE's platform must claim at least one
telemetry capability (gnmi, snmp or ssh_cli). This is where "the Cisco
has no gNMI" surfaces as *data*, not tribal knowledge - the platform
claims ssh_cli, so the observability compiler emits a CLI-scrape
collector instead of a gNMI subscription. A platform claiming nothing
fails the merge.
"""

from infrahub_sdk.checks import InfrahubCheck

TELEMETRY_CAPABILITIES = {"gnmi", "snmp", "ssh_cli", "bmp_client"}


class ObservabilityCapabilityCheck(InfrahubCheck):
    query = "observability_capability"

    def validate(self, data):
        for edge in data["IntentObservabilitySignal"]["edges"]:
            signal = edge["node"]
            contract = (signal.get("contract") or {}).get("node")
            if not contract:
                continue

            for pe_edge in contract["pe_devices"]["edges"]:
                device = pe_edge["node"]
                platform = (device.get("platform") or {}).get("node")
                if not platform:
                    self.log_error(
                        message=f"Signal '{signal['name']['value']}' requires telemetry "
                                f"from {device['name']['value']}, which has no platform "
                                f"assigned - capabilities unknown",
                    )
                    continue

                capabilities = {
                    c["node"]["name"]["value"]
                    for c in platform["capabilities"]["edges"]
                    if c["node"]["category"]["value"] == "telemetry"
                }
                if not capabilities & TELEMETRY_CAPABILITIES:
                    self.log_error(
                        message=f"Invariant violated (observability): signal "
                                f"'{signal['name']['value']}' on contract "
                                f"'{contract['name']['value']}' requires telemetry from "
                                f"{device['name']['value']}, but platform "
                                f"'{platform['name']['value']}' claims no telemetry "
                                f"capability (gnmi/snmp/ssh_cli/bmp_client)",
                    )
