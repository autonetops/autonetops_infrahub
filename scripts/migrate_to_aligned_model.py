#!/usr/bin/env python3
"""Purge pre-alignment intent data ahead of the vocabulary migration.

The ADR-0015/0017 alignment changes `IntentPolicy` and `IntentInvariant`
from node to generic and re-parents contracts from the retiring grouping
policy onto their intent - shapes InfraHub cannot morph in place. The
sequence is therefore: purge instance data (this script), retire the old
kinds, load the aligned model, re-seed:

    python scripts/migrate_to_aligned_model.py
    infrahubctl schema load schemas/migrations/retire_1_detach.yml
    infrahubctl schema load schemas/migrations/retire_2_kinds.yml
    infrahubctl schema load schemas/intent.yml schemas/operations.yml
    python scripts/bootstrap.py

Everything deleted here is bootstrap-seeded; nothing hand-authored lives
in these kinds. Inventory (devices, interfaces, cabling), IPAM, zones,
capabilities, tenants and groups are untouched.

Environment: INFRAHUB_ADDRESS and INFRAHUB_API_TOKEN (same as bootstrap).
"""

import os
import sys

from infrahub_sdk import Config, InfrahubClientSync

BRANCH = os.environ.get("INFRAHUB_BRANCH", "main")

# Leaf-first, so no delete ever hits a protected parent edge. Kinds that
# are already gone (fresh server, or a re-run after the schema loads)
# just report 0.
PURGE_KINDS = [
    "IntentObservabilitySignal",
    "IntentSecurityRule",
    "IntentRoutingContract",
    "IntentReachabilityContract",
    "IntentSecurityContract",
    "IntentObservabilityContract",
    "IntentReliabilityContract",
    "IntentInvariant",
    "IntentPolicy",
    "IntentIntent",
    "IntentRealm",
    "OpsChangeWindow",
]


def main():
    client = InfrahubClientSync(
        address=os.environ.get("INFRAHUB_ADDRESS", "http://localhost:8000"),
        config=Config(api_token=os.environ.get("INFRAHUB_API_TOKEN")),
    )
    for kind in PURGE_KINDS:
        try:
            nodes = client.all(kind=kind, branch=BRANCH)
        except Exception as exc:
            print(f"  {kind}: skipped ({str(exc).splitlines()[0]})")
            continue
        for node in nodes:
            node.delete()
        print(f"  {kind}: deleted {len(nodes)}")
    print("purge done - load the retire file, then the aligned schemas, "
          "then re-run bootstrap.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"purge failed: {exc}", file=sys.stderr)
        raise
