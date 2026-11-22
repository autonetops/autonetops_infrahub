"""Observability invariant: if you can't observe it, you can't claim compliance.

Every signal that watches a routing contract must be collectable on every
PE that contract attaches to: the PE's device type must claim at least one
telemetry capability (gnmi, snmp, ssh_cli or bmp_client). What a box can be
observed *with* is data in the SoT, not tribal knowledge - the cEOS PEs
claim gnmi and compile a subscription, while a device type claiming only
ssh_cli (Cisco IOL) compiles a CLI-scrape collector for the same signal.
A device type claiming nothing - or a device with no device type - fails
the merge.

Capabilities live on the device type, not the platform: one OS family spans
models that can do different things (schemas/intent.yml).

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
                device_type = (device.get("device_type") or {}).get("node")
                if not device_type:
                    self.log_error(
                        message=f"Signal '{signal['name']['value']}' requires telemetry "
                                f"from {device['name']['value']}, which has no device type "
                                f"assigned - capabilities unknown",
                    )
                    continue

                capabilities = {
                    c["node"]["name"]["value"]
                    for c in device_type["capabilities"]["edges"]
                    if c["node"]["category"]["value"] == "telemetry"
                }
                if not capabilities & TELEMETRY_CAPABILITIES:
                    self.log_error(
                        message=f"Invariant violated (capability_present): signal "
                                f"'{signal['name']['value']}' watches contract "
                                f"'{watched['name']['value']}' and requires telemetry from "
                                f"{device['name']['value']}, but device type "
                                f"'{device_type['name']['value']}' claims no telemetry "
                                f"capability (gnmi/snmp/ssh_cli/bmp_client)",
                    )
