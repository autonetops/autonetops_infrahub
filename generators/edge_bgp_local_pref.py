"""Swap DcimDevice.bgp_local_pref between the two members of ``edge_bgp``.

Infrahub runs a generator once per target, so this runs twice (once per
member). A naive "set mine to my peer's value" would race: whichever run lands
second reads an already-swapped peer and both devices end up equal. Instead the
*lead* member (lowest name) performs the swap for the pair in one go and the
other run is a no-op.

Note: a swap is deliberately NOT idempotent - every execution flips the pair
again (manual run, proposed-change pipeline, post-merge run).

Tracking: generator runs are wrapped in ``start_tracking(delete_unused_nodes=True)``.
Anything saved through the tracked client joins this generator's group and is
DELETED by a later run that does not touch it. These devices are pre-existing
objects the generator merely edits, so every save opts out of the group context.
"""

from infrahub_sdk.generator import InfrahubGenerator


class EdgeBgpLocalPrefGenerator(InfrahubGenerator):
    async def generate(self, data: dict) -> None:
        target_edges = data["DcimDevice"]["edges"]
        if not target_edges:
            raise ValueError(f"Target device not found: {self.params}")
        target_name = target_edges[0]["node"]["name"]["value"]

        groups = data["CoreStandardGroup"]["edges"]
        if not groups:
            raise ValueError("Group 'edge_bgp' not found")
        members = [
            m["node"]
            for m in groups[0]["node"]["members"]["edges"]
            if "name" in m["node"]  # DcimDevice members only
        ]
        if len(members) != 2:
            raise ValueError(
                f"edge_bgp must contain exactly 2 DcimDevice members to swap, found {len(members)}: "
                f"{[m['name']['value'] for m in members]}"
            )

        first, second = sorted(members, key=lambda m: m["name"]["value"])
        if target_name != first["name"]["value"]:
            self.logger.info(
                f"{target_name}: not the lead member; {first['name']['value']} swaps the pair. Nothing to do."
            )
            return

        a_val = first["bgp_local_pref"]["value"]
        b_val = second["bgp_local_pref"]["value"]
        if a_val == b_val:
            self.logger.info(f"Both members have bgp_local_pref={a_val}; nothing to swap.")
            return

        for member, new_val in ((first, b_val), (second, a_val)):
            device = await self.client.get(kind="DcimDevice", id=member["id"], branch=self.branch_name)
            old_val = device.bgp_local_pref.value
            device.bgp_local_pref.value = new_val
            await device.save(update_group_context=False)
            self.logger.info(f"{member['name']['value']}: bgp_local_pref {old_val} -> {new_val}")
