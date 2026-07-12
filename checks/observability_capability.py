"""Observability invariant: if you can't observe it, you can't claim compliance.

Every signal that watches a routing contract must be collectable on every
PE that contract attaches to: the PE's platform must claim at least one
telemetry capability (gnmi, snmp or ssh_cli). What a platform can be
observed *with* is data in the SoT, not tribal knowledge - the cEOS PEs
claim gnmi and compile a subscription, while a platform claiming only
ssh_cli (Cisco IOL) compiles a CLI-scrape collector for the same signal.
A platform claiming nothing fails the merge.

Note the scope: this guards the ``pe_devices`` of the *watched* contract
(signal -> watches -> routing contract -> pe_devices), because that is
where the signal is collected.
"""

from infrahub_sdk.checks import InfrahubCheck

TELEMETRY_CAPABILITIES = {"gnmi", "snmp", "ssh_cli", "bmp_client"}


class ObservabilityCapabilityCheck(InfrahubCheck):
    query = "observability_capability"

    def validate(self, data):
        for edge in data["IntentObservabilitySignal"]["edges"]:
            signal = edge["node"]
            watched = (signal.get("watches") or {}).get("node")
            if not watched or "pe_devices" not in watched:
                # fleet signals watch nothing in particular
                continue

            for pe_edge in watched["pe_devices"]["edges"]:
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
                        message=f"Invariant violated (capability_present): signal "
                                f"'{signal['name']['value']}' watches contract "
                                f"'{watched['name']['value']}' and requires telemetry from "
                                f"{device['name']['value']}, but platform "
                                f"'{platform['name']['value']}' claims no telemetry "
                                f"capability (gnmi/snmp/ssh_cli/bmp_client)",
                    )
