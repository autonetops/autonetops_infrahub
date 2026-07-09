#!/usr/bin/env python3
"""Push compiled device configurations to the containerlab nodes.

Stand-in for the execution layer (deliberately out of scope): a one-shot
push of build/configs/*.cfg produced by fetch_artifacts.py. Transport is
selected per platform - the same capability-driven dispatch the
compilers use, at the delivery stage:

    cisco_iosxe / arista_eos -> SSH CLI (netmiko)
    nokia_srlinux            -> SSH sr_cli (set-command file)
    frr                      -> docker exec vtysh (no SSH daemon needed)

In a production system this would be a runner reacting to InfraHub merge
events, followed by the validator loop comparing ContractExpectations
against telemetry. The lab keeps that loop human-in-the-middle.

Usage: python scripts/deploy_configs.py [device ...]
"""

import os
import pathlib
import subprocess
import sys

from netmiko import ConnectHandler

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "build" / "configs"

SSH_USER = os.environ.get("SSH_USER", "admin")
SSH_PASSWORD = os.environ.get("SSH_PASSWORD", "admin")
SRL_PASSWORD = os.environ.get("SRL_PASSWORD", "NokiaSrl1!")

# device -> (platform, mgmt address / container name)
INVENTORY = {
    "pe-emea-01": ("cisco_iosxe", "172.20.20.11"),
    "pe-emea-02": ("arista_eos", "172.20.20.12"),
    "core-rr-01": ("nokia_srlinux", "172.20.20.13"),
    "ce-custc-01": ("frr", "clab-intent-lab-ce-custc-01"),
    "ce-custc-02": ("frr", "clab-intent-lab-ce-custc-02"),
    "peer-inet-01": ("frr", "clab-intent-lab-peer-inet-01"),
}

NETMIKO_TYPES = {"cisco_iosxe": "cisco_ios", "arista_eos": "arista_eos"}


def push_cli(platform, host, config):
    conn = ConnectHandler(
        device_type=NETMIKO_TYPES[platform],
        host=host,
        username=SSH_USER,
        password=SSH_PASSWORD,
        secret=SSH_PASSWORD,
    )
    conn.enable()
    output = conn.send_config_set(config.splitlines(), cmd_verify=False)
    if platform == "cisco_iosxe":
        conn.save_config()
    else:
        conn.send_command("write memory")
    conn.disconnect()
    return output


def push_srlinux(host, config):
    commands = "\n".join(
        line for line in config.splitlines()
        if line.strip() and not line.startswith("#")
    )
    script = f"enter candidate\n{commands}\ncommit now\n"
    return subprocess.run(
        ["sshpass", "-p", SRL_PASSWORD, "ssh",
         "-o", "StrictHostKeyChecking=no", f"admin@{host}", "sr_cli"],
        input=script, capture_output=True, text=True, check=True,
    ).stdout


def push_frr(container, config):
    body = "\n".join(
        line for line in config.splitlines() if not line.startswith("!")
    )
    return subprocess.run(
        ["docker", "exec", "-i", container, "vtysh"],
        input=f"configure terminal\n{body}\nend\nwrite memory\n",
        capture_output=True, text=True, check=True,
    ).stdout


def main():
    selection = sys.argv[1:] or list(INVENTORY)
    for device in selection:
        platform, target = INVENTORY[device]
        path = CONFIG_DIR / f"{device}.cfg"
        if not path.exists():
            print(f"  {device}: no compiled config at {path}, skipping")
            continue
        config = path.read_text()
        print(f"==> {device} ({platform})")
        try:
            if platform in NETMIKO_TYPES:
                push_cli(platform, target, config)
            elif platform == "nokia_srlinux":
                push_srlinux(target, config)
            elif platform == "frr":
                push_frr(target, config)
        except Exception as exc:
            print(f"  FAILED: {exc}", file=sys.stderr)
            continue
        print("  ok")


if __name__ == "__main__":
    main()
