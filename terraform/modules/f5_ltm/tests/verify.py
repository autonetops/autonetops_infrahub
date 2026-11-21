"""Compare an f5-ltm-configuration YAML with what a BIG-IP actually holds.

    F5_ADDRESS=10.10.10.121 F5_USERNAME=... F5_PASSWORD=... \
        python verify.py config.yml [--absent kind:/Partition/name ...]

Reads every object the YAML lists over iControl REST and checks each field the
f5_ltm module sets, independently of Terraform (whose own read-back is what is
under test). ``--absent`` asserts objects were deleted; kind is one of
monitor/<type>, node, pool, snatpool, virtual. Exit 1 on any mismatch.
Standard library only.
"""

import json
import os
import ssl
import sys
import urllib.error
import urllib.request

import yaml

BASE = f"https://{os.environ['F5_ADDRESS']}"
CTX = ssl._create_unverified_context()  # lab box, self-signed management certificate


def call(method, path, body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-F5-Auth-Token"] = token
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body else None,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, {}


def login():
    status, body = call(
        "POST",
        "/mgmt/shared/authn/login",
        {
            "username": os.environ["F5_USERNAME"],
            "password": os.environ["F5_PASSWORD"],
            "loginProviderName": "tmos",
        },
    )
    if status != 200:
        sys.exit(f"login failed: HTTP {status}")
    return body["token"]["token"]


def rest(path):
    """`/Common/name` -> `~Common~name` (iControl REST object path)."""
    return path.replace("/", "~")


class Checker:
    def __init__(self, token):
        self.token = token
        self.checks = 0
        self.failures = []

    def get(self, uri):
        status, body = call("GET", uri, token=self.token)
        return body if status == 200 else None

    def eq(self, what, got, want):
        self.checks += 1
        if got != want:
            self.failures.append(f"{what}: box has {got!r}, config wants {want!r}")

    def present(self, what, obj):
        self.checks += 1
        if obj is None:
            self.failures.append(f"{what}: missing on the box")
            return False
        return True

    def monitor(self, path, m):
        kind = m["parent"].rsplit("/", 1)[-1]
        box = self.get(f"/mgmt/tm/ltm/monitor/{kind}/{rest(path)}")
        if not self.present(f"monitor {path}", box):
            return
        self.eq(f"monitor {path} parent", box.get("defaultsFrom"), m["parent"])
        for key, field in (
            ("interval", "interval"),
            ("timeout", "timeout"),
            ("send", "send"),
            ("receive", "recv"),
        ):
            if key in m:
                self.eq(f"monitor {path} {key}", box.get(field), m[key])

    def node(self, path, n):
        box = self.get(f"/mgmt/tm/ltm/node/{rest(path)}")
        if not self.present(f"node {path}", box):
            return
        self.eq(f"node {path} address", box.get("address"), n["address"])
        self.eq(f"node {path} monitor", box.get("monitor", "").strip(), n["monitor"])

    def pool(self, path, p):
        box = self.get(f"/mgmt/tm/ltm/pool/{rest(path)}?expandSubcollections=true")
        if not self.present(f"pool {path}", box):
            return
        for key, field in (
            ("description", "description"),
            ("load_balancing_mode", "loadBalancingMode"),
            ("minimum_active_members", "minActiveMembers"),
            ("slow_ramp_time", "slowRampTime"),
            ("service_down_action", "serviceDownAction"),
            ("reselect_tries", "reselectTries"),
            ("allow_snat", "allowSnat"),
            ("allow_nat", "allowNat"),
        ):
            self.eq(f"pool {path} {key}", box.get(field), p[key])
        # REST: "monitor": "/Common/a and /Common/b " or absent.
        monitors = sorted(
            x for x in box.get("monitor", "").split() if x not in ("and", "")
        )
        self.eq(f"pool {path} monitors", monitors, sorted(p["monitors"]))
        members = {
            m["fullPath"]: m for m in box.get("membersReference", {}).get("items", [])
        }
        self.eq(f"pool {path} members", sorted(members), sorted(p["members"]))
        for name, want in p["members"].items():
            got = members.get(name)
            if got is None:
                continue
            self.eq(f"member {path} {name} ratio", got.get("ratio"), want["ratio"])
            self.eq(
                f"member {path} {name} priority_group",
                got.get("priorityGroup"),
                want["priority_group"],
            )
            self.eq(
                f"member {path} {name} connection_limit",
                got.get("connectionLimit"),
                want["connection_limit"],
            )
            # An enabled member of a monitored pool reports monitor-enabled.
            session = got.get("session")
            self.eq(
                f"member {path} {name} state",
                "enabled"
                if session in ("user-enabled", "monitor-enabled")
                else "disabled",
                want["state"],
            )

    def snatpool(self, path, s):
        box = self.get(f"/mgmt/tm/ltm/snatpool/{rest(path)}")
        if not self.present(f"snatpool {path}", box):
            return
        self.eq(
            f"snatpool {path} members", sorted(box.get("members", [])), s["members"]
        )

    def virtual(self, path, v):
        box = self.get(f"/mgmt/tm/ltm/virtual/{rest(path)}?expandSubcollections=true")
        if not self.present(f"virtual {path}", box):
            return
        partition = path.split("/")[1]
        sep = "." if ":" in v["destination"] else ":"
        self.eq(
            f"virtual {path} destination",
            box.get("destination"),
            f"/{partition}/{v['destination']}{sep}{v['port']}",
        )
        for key, field in (
            ("description", "description"),
            ("mask", "mask"),
            ("ip_protocol", "ipProtocol"),
            ("source", "source"),
            ("pool", "pool"),
            ("translate_address", "translateAddress"),
            ("translate_port", "translatePort"),
            ("connection_limit", "connectionLimit"),
            ("source_port", "sourcePort"),
        ):
            self.eq(f"virtual {path} {key}", box.get(field), v[key])
        profiles = {
            p["fullPath"]: p.get("context")
            for p in box.get("profilesReference", {}).get("items", [])
        }
        want = {p: "all" for p in v["profiles"]}
        want.update({p: "clientside" for p in v["client_profiles"]})
        want.update({p: "serverside" for p in v["server_profiles"]})
        self.eq(f"virtual {path} profiles", profiles, want)
        self.eq(
            f"virtual {path} persistence",
            sorted(f"/{p['partition']}/{p['name']}" for p in box.get("persist", [])),
            sorted(v["persistence_profiles"]),
        )
        self.eq(f"virtual {path} irules", box.get("rules", []), v["irules"])
        snat = box.get("sourceAddressTranslation", {})
        self.eq(
            f"virtual {path} source_address_translation",
            snat.get("type"),
            v["source_address_translation"],
        )
        self.eq(f"virtual {path} snatpool", snat.get("pool"), v["snatpool"])
        self.eq(f"virtual {path} vlans", sorted(box.get("vlans", [])), v["vlans"])
        self.eq(
            f"virtual {path} vlans_enabled",
            bool(box.get("vlansEnabled")),
            v["vlans_enabled"],
        )
        self.eq(
            f"virtual {path} state",
            "disabled" if box.get("disabled") else "enabled",
            v["state"],
        )

    def absent(self, spec):
        kind, path = spec.split(":", 1)
        self.checks += 1
        if self.get(f"/mgmt/tm/ltm/{kind}/{rest(path)}") is not None:
            self.failures.append(f"{kind} {path}: still on the box, should be deleted")


def main(argv):
    cfg = yaml.safe_load(open(argv[0]))
    absent = []
    if "--absent" in argv:
        absent = argv[argv.index("--absent") + 1 :]
    c = Checker(login())
    for kind in ("monitors", "nodes", "pools", "snatpools", "virtual_servers"):
        check = getattr(
            c,
            {
                "monitors": "monitor",
                "nodes": "node",
                "pools": "pool",
                "snatpools": "snatpool",
                "virtual_servers": "virtual",
            }[kind],
        )
        for path, obj in cfg[kind].items():
            check(path, obj)
    for spec in absent:
        c.absent(spec)
    for f in c.failures:
        print("MISMATCH", f)
    print(f"{argv[0]}: {c.checks} checks, {len(c.failures)} mismatches")
    return 1 if c.failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
