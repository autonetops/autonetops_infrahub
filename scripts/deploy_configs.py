#!/usr/bin/env python3
"""Push compiled device configurations to the lab nodes.

Stand-in for the execution layer (the real one lives in autonetops_ibn):
a one-shot push of build/configs/*.cfg produced by fetch_artifacts.py.
Transport is selected per platform - the same capability-driven dispatch
the compilers use, at the delivery stage:

    arista_eos / cisco_iosxe -> SSH CLI (netmiko)
    juniper_junos            -> SSH CLI (netmiko), `set` lines + commit
    nokia_srlinux            -> SSH CLI (netmiko nokia_srl): candidate
                                private, `set` lines, commit stay + save.
                                Falls back to the factory NokiaSrl1!
                                password and converges the box onto
                                SRL_USER/SRL_PASSWORD as part of the push.
    frr                      -> docker exec vtysh when the container runs
                                locally; skipped otherwise (no sshd)

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
import time

from netmiko import ConnectHandler

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "build" / "configs"

SSH_USER = os.environ.get("SSH_USER", "admin")
SSH_PASSWORD = os.environ.get("SSH_PASSWORD", "admin")
# containerlab seeds vJunos with admin/admin@123
JUNOS_USER = os.environ.get("JUNOS_USER", "admin")
JUNOS_PASSWORD = os.environ.get("JUNOS_PASSWORD", "admin@123")
# SR Linux ships with admin/NokiaSrl1! from the factory; the push tries
# SRL_PASSWORD first, falls back to the factory password, and sets the
# admin password to SRL_PASSWORD so external access (SSH/gNMI) converges
# on admin/admin.
SRL_USER = os.environ.get("SRL_USER", "admin")
SRL_PASSWORD = os.environ.get("SRL_PASSWORD", "admin")
SRL_FACTORY_PASSWORD = os.environ.get("SRL_FACTORY_PASSWORD", "NokiaSrl1!")

# device -> (platform, ssh address, frr container name when local)
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
    """FRR has no sshd; only deliverable when its container runs locally."""
    probe = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", container],
        capture_output=True, text=True,
    )
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        raise RuntimeError(
            f"container {container} not running locally - FRR is only "
            f"deliverable from the containerlab host"
        )
    body = "\n".join(
        line for line in config.splitlines() if not line.startswith("!")
    )
    return subprocess.run(
        ["docker", "exec", "-i", container, "vtysh"],
        input=f"configure terminal\n{body}\nend\nwrite memory\n",
        capture_output=True, text=True, check=True,
    ).stdout


def _srl_mode_banner(conn):
    """SR Linux prints a `--{ <mode> }--[ <context> ]--` banner above every
    prompt; a bare newline is the cheapest way to read the current mode."""
    conn.write_channel("\n")
    time.sleep(0.7)
    return conn.read_channel()


def _srl_enter_candidate(conn, attempts=3):
    """Enter (and VERIFY) a private candidate. On a busy box the first
    command after login can get merged with netmiko's session-prep lines and
    silently never execute - so check the mode banner and retry."""
    _srl_mode_banner(conn)  # settle the channel first
    for _ in range(attempts):
        conn.send_command_timing("enter candidate private")
        if "candidate" in _srl_mode_banner(conn):
            return
        time.sleep(1)
    raise RuntimeError("could not enter SR Linux candidate mode")


def push_srl(host, config):
    """Deliver an SR Linux config over SSH (netmiko ``nokia_srl``).

    The driver enters a *private* candidate (isolated from other sessions),
    sources the ``set`` lines, then ``commit stay`` + ``save startup``. The
    push tries SRL_PASSWORD first and falls back to the factory password;
    either way the admin password is set as part of the payload so the box
    converges on SRL_USER/SRL_PASSWORD for SSH and gNMI.
    """
    lines = [f"set / system aaa authentication admin-user password {SRL_PASSWORD}"]
    lines += [
        line for line in config.splitlines()
        if line.strip() and not line.startswith("#")
    ]

    conn = None
    for password in (SRL_PASSWORD, SRL_FACTORY_PASSWORD):
        try:
            conn = ConnectHandler(
                device_type="nokia_srl", host=host,
                username=SRL_USER, password=password,
            )
            break
        except Exception:
            conn = None
    if conn is None:
        raise RuntimeError(f"SSH authentication failed for {SRL_USER}@{host}")

    try:
        # timing-based sends: SR Linux's two-line prompt plus ANSI noise makes
        # netmiko's echo verification flaky, and a private candidate isolates
        # the push anyway - errors are collected and checked at the end.
        _srl_enter_candidate(conn)
        # The named private candidate persists across sessions, so discard
        # whatever a previous (possibly failed) push left in it first.
        output = conn.send_command_timing("discard stay")
        for line in lines:
            output += conn.send_command_timing(line)
        low = output.lower()
        markers = ("error", "unknown token", "invalid value", "failed")
        if any(m in low for m in markers):
            conn.send_command_timing("discard stay")
            raise RuntimeError(f"candidate rejected:\n{output.strip()[-800:]}")
        committed = conn.send_command_timing("commit save")
        if "committed" not in committed.lower():
            raise RuntimeError(f"no commit confirmation:\n{committed.strip()[-400:]}")
        output += committed
    finally:
        conn.disconnect()
    return output


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
