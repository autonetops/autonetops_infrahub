"""Move IntentCapability assignments from DcimPlatform to DcimDeviceType.

Capabilities describe what a *model* can do, so they hang off the device
type (schemas/intent.yml). This script, idempotent, on INFRAHUB_BRANCH
(default main):

1. creates the device types the containerlab nodes were missing (cEOS,
   7220 IXR-D3L, Cisco IOL) and assigns them to the intent-lab devices;
2. gives every device type with no capabilities yet the capabilities of
   its platform - the behaviour-preserving copy. Device types that already
   carry capabilities are left alone.

Run it before loading schemas/migrations/retire_platform_capabilities.yml,
which deletes DcimPlatform.capabilities (and its data).

    INFRAHUB_BRANCH=main uv run python scripts/migrate_capabilities_to_device_type.py
"""

import os

from infrahub_sdk import Config, InfrahubClientSync

BRANCH = os.environ.get("INFRAHUB_BRANCH", "main")

# (device type, manufacturer, platform, devices)
LAB_DEVICE_TYPES = [
    ("cEOS", "Arista Networks", "arista_eos", ["PE-EMEA-01", "PE-EMEA-02"]),
    ("7220 IXR-D3L", "Nokia", "nokia_srlinux", ["ce-custc-01", "ce-custc-02"]),
    ("Cisco IOL", "Cisco", "cisco_iosxe", ["core-rr-01"]),
]


def main():
    client = InfrahubClientSync(config=Config(
        address=os.environ.get("INFRAHUB_ADDRESS", "http://localhost:8000"),
        api_token=os.environ.get("INFRAHUB_API_TOKEN") or os.environ["INFRAHUB_API_KEY"],
        default_branch=BRANCH,
    ))

    for dt_name, mfr_name, platform_name, devices in LAB_DEVICE_TYPES:
        mfr = client.get("OrganizationManufacturer", name__value=mfr_name, raise_when_missing=False)
        if not mfr:
            mfr = client.create("OrganizationManufacturer", name=mfr_name)
            mfr.save()
            print(f"created manufacturer {mfr_name}")
        platform = client.get("DcimPlatform", name__value=platform_name)
        dt = client.get("DcimDeviceType", name__value=dt_name, raise_when_missing=False)
        if not dt:
            dt = client.create("DcimDeviceType", name=dt_name, manufacturer=mfr, platform=platform)
            dt.save()
            print(f"created device type {dt_name}")
        for dev_name in devices:
            dev = client.get("DcimDevice", name__value=dev_name, raise_when_missing=False)
            if not dev:
                print(f"skip {dev_name}: not in {BRANCH}")
                continue
            if dev.device_type.id != dt.id:
                dev.device_type = dt
                dev.save()
                print(f"{dev_name}: device_type -> {dt_name}")

    platform_caps = {
        p.id: [c.id for c in p.capabilities.peers]
        for p in client.all("DcimPlatform", include=["capabilities"])
    }
    for dt in client.all("DcimDeviceType", include=["capabilities"]):
        if dt.capabilities.peers:
            continue
        caps = platform_caps.get(dt.platform.id) if dt.platform.id else None
        if not caps:
            continue
        for cap_id in caps:
            dt.capabilities.add(cap_id)
        dt.save()
        print(f"{dt.name.value}: {len(caps)} capabilities copied from its platform")


if __name__ == "__main__":
    main()
