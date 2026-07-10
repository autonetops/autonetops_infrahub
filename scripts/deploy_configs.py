#!/usr/bin/env python3
"""Push compiled device configurations to the containerlab nodes.

Stand-in for the execution layer (deliberately out of scope): a one-shot
push of build/configs/*.cfg produced by fetch_artifacts.py. Transport is
selected per platform - the same capability-driven dispatch the
compilers use, at the delivery stage:

    arista_eos / cisco_iosxe -> SSH CLI (netmiko)
    juniper_junos            -> SSH CLI (netmiko), `set` lines + commit
    nokia_srlinux            -> docker exec sr_cli (its ANSI CLI does not
                                screen-scrape over SSH), `set` lines + commit
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

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "build" / "configs"

SSH_USER = os.environ.get("SSH_USER", "admin")
SSH_PASSWORD = os.environ.get("SSH_PASSWORD", "admin")
# containerlab seeds vJunos with admin/admin@123
JUNOS_USER = os.environ.get("JUNOS_USER", "admin")
JUNOS_PASSWORD = os.environ.get("JUNOS_PASSWORD", "admin@123")
# SR Linux is driven via `docker exec sr_cli` (see push_srl), not SSH, so
# delivery needs no credentials. The push sets the admin password to
# SRL_PASSWORD so external access (SSH/gNMI) still lands on admin/admin.
SRL_USER = os.environ.get("SRL_USER", "admin")
SRL_PASSWORD = os.environ.get("SRL_PASSWORD", "admin")

# device -> (platform, ssh address | container name)
INVENTORY = {
    "pe-emea-01": ("arista_eos", "172.20.20.11"),
    "pe-emea-02": ("arista_eos", "172.20.20.12"),
    "core-rr-01": ("cisco_iosxe", "172.20.20.13"),
    "ce-custc-01": ("nokia_srlinux", "clab-intent-lab-ce-custc-01"),
    "ce-custc-02": ("nokia_srlinux", "clab-intent-lab-ce-custc-02"),
    "peer-inet-01": ("frr", "clab-intent-lab-peer-inet-01"),
}

NETMIKO_TYPES = {
    "cisco_iosxe": "cisco_ios",
    "arista_eos": "arista_eos",
    "juniper_junos": "juniper_junos",
}

# platforms whose config is a candidate that has to be committed, and
# whose drivers therefore have no enable mode
CANDIDATE_PLATFORMS = ("juniper_junos",)

CREDENTIALS = {
    "juniper_junos": (JUNOS_USER, JUNOS_PASSWORD),
}


def _config_lines(platform, config):
    """Strip the artifact's comment header - Junos treats a bare `#` line
    as a comment only in a config file, not at the CLI prompt."""
    if platform in CANDIDATE_PLATFORMS:
        return [
            line for line in config.splitlines()
            if line.strip() and not line.startswith("#")
        ]
    return config.splitlines()


def push_cli(platform, host, config):
    username, password = CREDENTIALS.get(platform, (SSH_USER, SSH_PASSWORD))
    kwargs = {
        "device_type": NETMIKO_TYPES[platform],
        "host": host,
        "username": username,
        "password": password,
    }
    candidate = platform in CANDIDATE_PLATFORMS
    if not candidate:
        kwargs["secret"] = password

    conn = ConnectHandler(**kwargs)
    try:
        if not candidate:
            conn.enable()
        output = conn.send_config_set(
            _config_lines(platform, config), cmd_verify=False
        )
        if candidate:
            output += conn.commit()  # juniper_junos: `commit`
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


def push_srl(container, config):
    """Deliver an SR Linux config via `docker exec sr_cli`.

    SR Linux's ANSI-heavy interactive CLI does not screen-scrape reliably
    over SSH (netmiko cannot track the candidate-mode prompt), so - as with
    FRR - the lab drives the container directly. Delivery needs no
    credentials; the admin password is set as the first line so external
    access (SSH/gNMI) still lands on SRL_USER/SRL_PASSWORD.
    """
    lines = [f"set / system aaa authentication admin-user password {SRL_PASSWORD}"]
    lines += [
        line for line in config.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    subprocess.run(
        ["docker", "exec", "-i", container, "sh", "-c",
         "cat > /tmp/infrahub-config.cli"],
        input="\n".join(lines), capture_output=True, text=True, check=True,
    )
    # A *private* candidate isolates this push. SR Linux's shared default
    # candidate (`-e`) keeps a partially-applied `source` across sr_cli
    # invocations, so one failed push poisons the next (its leftover lines
    # ride along into the following commit); `-E private` is discarded when
    # the process exits without committing.
    result = subprocess.run(
        ["docker", "exec", container, "sr_cli", "-E", "private",
         "--post-command", "commit save", "source /tmp/infrahub-config.cli"],
        capture_output=True, text=True, check=False,
    )
    out = result.stdout + result.stderr
    # check=False above: sr_cli exits non-zero on a source parse error but 0
    # on some commit-time failures, so the exit code alone is unreliable.
    # Scan the output for the commit confirmation and SR Linux's error
    # phrasings instead - this also surfaces sr_cli's own message (e.g.
    # "Failed to parse value 'None'") rather than a bare CalledProcessError.
    low = out.lower()
    markers = ("error", "unknown token", "invalid value", "failed")
    if "committed" not in low or any(m in low for m in markers):
        raise RuntimeError(out.strip() or "sr_cli produced no commit confirmation")
    return out


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
            if platform == "nokia_srlinux":
                push_srl(target, config)
            elif platform in NETMIKO_TYPES:
                push_cli(platform, target, config)
            elif platform == "frr":
                push_frr(target, config)
        except Exception as exc:
            print(f"  FAILED: {exc}", file=sys.stderr)
            continue
        print("  ok")


if __name__ == "__main__":
    main()
