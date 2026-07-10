#!/usr/bin/env python3
"""Push compiled device configurations to the containerlab nodes.

Stand-in for the execution layer (deliberately out of scope): a one-shot
push of build/configs/*.cfg produced by fetch_artifacts.py. Transport is
selected per platform - the same capability-driven dispatch the
compilers use, at the delivery stage:

    arista_eos / cisco_iosxe -> SSH CLI (netmiko)
    nokia_srlinux            -> SSH CLI (netmiko), `set` lines + commit
    juniper_junos            -> SSH CLI (netmiko), `set` lines + commit
    frr                      -> docker exec vtysh (no SSH daemon needed)

Every platform here also claims `netconf`, and a production runner would
prefer it (or gNMI Set on the cEOS PEs) over screen-scraping a CLI. The
lab stays on netmiko to keep the dependency surface at one library.

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
from netmiko.exceptions import NetmikoAuthenticationException

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "build" / "configs"

SSH_USER = os.environ.get("SSH_USER", "admin")
SSH_PASSWORD = os.environ.get("SSH_PASSWORD", "admin")
# containerlab seeds vJunos with admin/admin@123
JUNOS_USER = os.environ.get("JUNOS_USER", "admin")
JUNOS_PASSWORD = os.environ.get("JUNOS_PASSWORD", "admin@123")
# We want SR Linux on admin/admin, but containerlab boots it as
# admin/NokiaSrl1! and does not reliably apply a startup-config that
# changes it. So this pusher owns the credential: it connects with the
# target password, falls back to the clab default, and the first push
# carries the password change so the box converges to admin/admin.
SRL_USER = os.environ.get("SRL_USER", "admin")
SRL_PASSWORD = os.environ.get("SRL_PASSWORD", "admin")
SRL_DEFAULT_PASSWORD = os.environ.get("SRL_DEFAULT_PASSWORD", "NokiaSrl1!")

# device -> (platform, mgmt address / container name)
INVENTORY = {
    "pe-emea-01": ("arista_eos", "172.20.20.11"),
    "pe-emea-02": ("arista_eos", "172.20.20.12"),
    "core-rr-01": ("cisco_iosxe", "172.20.20.13"),
    "ce-custc-01": ("nokia_srlinux", "172.20.20.21"),
    "ce-custc-02": ("nokia_srlinux", "172.20.20.22"),
    "peer-inet-01": ("frr", "clab-intent-lab-peer-inet-01"),
}

NETMIKO_TYPES = {
    "cisco_iosxe": "cisco_ios",
    "arista_eos": "arista_eos",
    "nokia_srlinux": "nokia_srl",
    "juniper_junos": "juniper_junos",
}

# platforms whose config is a candidate that has to be committed, and
# whose drivers therefore have no enable mode
CANDIDATE_PLATFORMS = ("nokia_srlinux", "juniper_junos")

CREDENTIALS = {
    "nokia_srlinux": (SRL_USER, SRL_PASSWORD),
    "juniper_junos": (JUNOS_USER, JUNOS_PASSWORD),
}

# passwords to try when connecting, in order. SR Linux may still be on the
# containerlab default the first time we reach it, before the push below
# sets it to SRL_PASSWORD.
LOGIN_PASSWORDS = {
    "nokia_srlinux": [SRL_PASSWORD, SRL_DEFAULT_PASSWORD],
}


def _config_lines(platform, config):
    """Strip the artifact's comment header - Junos and sr_cli both treat a
    bare `#` line as a comment only in a config file, not at the CLI
    prompt. For SR Linux, prepend the admin password so every push leaves
    the box on SRL_USER/SRL_PASSWORD regardless of how it booted."""
    if platform in CANDIDATE_PLATFORMS:
        lines = [
            line for line in config.splitlines()
            if line.strip() and not line.startswith("#")
        ]
        if platform == "nokia_srlinux":
            lines.insert(
                0,
                f"set / system aaa authentication admin-user password {SRL_PASSWORD}",
            )
        return lines
    return config.splitlines()


def push_cli(platform, host, config):
    username, password = CREDENTIALS.get(platform, (SSH_USER, SSH_PASSWORD))
    candidate = platform in CANDIDATE_PLATFORMS

    conn = None
    last_err = None
    for pw in LOGIN_PASSWORDS.get(platform, [password]):
        kwargs = {
            "device_type": NETMIKO_TYPES[platform],
            "host": host,
            "username": username,
            "password": pw,
        }
        if not candidate:
            kwargs["secret"] = pw
        try:
            conn = ConnectHandler(**kwargs)
            break
        except NetmikoAuthenticationException as err:
            last_err = err
    if conn is None:
        raise last_err

    try:
        if not candidate:
            conn.enable()
        output = conn.send_config_set(
            _config_lines(platform, config), cmd_verify=False
        )
        if candidate:
            # nokia_srl commits with `commit stay`, juniper_junos with
            # `commit` - netmiko owns that difference
            output += conn.commit()
            if platform == "nokia_srlinux":
                conn.save_config()  # `save startup`
        elif platform == "cisco_iosxe":
            conn.save_config()
        else:
            conn.send_command("write memory")
    finally:
        conn.disconnect()
    return output


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
            elif platform == "frr":
                push_frr(target, config)
        except Exception as exc:
            print(f"  FAILED: {exc}", file=sys.stderr)
            continue
        print("  ok")


if __name__ == "__main__":
    main()
