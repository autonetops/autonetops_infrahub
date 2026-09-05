"""Sectioned artifact: the SNMP slice of a device's configuration.

First of the per-section artifacts (device-snmp today; device-acl,
device-interfaces, device-base-configuration follow the same pattern):
one section, one artifact, one independently pushable + diffable +
pinnable unit. The monolithic device-configuration artifact stays — it
is the full intended state for audit and config-replace — while the
sections are the units a change plan actually ships.

Rides the same ``device_config`` query as the full renderer, so intent
is read once and identically; only the rendering scope differs. The
community VALUE comes from IntentSnmpCommunity data - never from this
repository.
"""

from infrahub_sdk.transforms import InfrahubTransform

SNMP_LINE = {
    "cisco_iosxe": "snmp-server community {community} {access}",
    "arista_eos": "snmp-server community {community} {access}",
}


def _v(attr):
    return attr.get("value") if attr else None


def _edges(rel):
    return [e["node"] for e in rel["edges"]] if rel else []


class DeviceSnmpTransform(InfrahubTransform):
    query = "device_config"

    async def transform(self, data):
        node = data["DcimDevice"]["edges"][0]["node"]
        platform_rel = node.get("platform") or {}
        platform_node = platform_rel.get("node") or {}
        platform = _v(platform_node.get("name")) or ""
        name = _v(node["name"])

        template = SNMP_LINE.get(platform)
        if template is None:
            return (
                f"! No SNMP renderer for platform '{platform}' "
                f"(device {name}) - section intentionally empty.\n"
            )
        lines = [
            "! Compiled by InfraHub - intent artifact (snmp section), "
            "do not hand-edit",
        ]
        for entry in _edges(data.get("IntentSnmpCommunity")):
            community = _v(entry.get("community_string"))
            if not community:
                continue
            access = (_v(entry.get("access")) or "ro").lower()
            if platform == "cisco_iosxe":
                access = access.upper()
            lines.append(template.format(community=community, access=access))
        if len(lines) == 1:
            lines.append("! no communities declared in intent")
        return "\n".join(lines) + "\n"
