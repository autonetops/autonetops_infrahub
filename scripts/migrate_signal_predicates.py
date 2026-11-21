#!/usr/bin/env python3
"""Backfill typed predicates on IntentObservabilitySignal (ADR-0002 step 2).

Resolves each signal's legacy text ``condition`` with the SAME parser the
compilers use and writes the result into operator / threshold /
threshold_ref / for_seconds. The text stays as the human note.
Idempotent; skips signals that already have an operator.

    INFRAHUB_BRANCH=typed-signal-predicates python scripts/migrate_signal_predicates.py
"""

import os
import pathlib
import sys

from infrahub_sdk import InfrahubClientSync

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "transforms"))
import signal_catalog  # noqa: E402

BRANCH = os.environ.get("INFRAHUB_BRANCH", "main")


def main():
    client = InfrahubClientSync()
    if BRANCH != "main" and BRANCH not in client.branch.all():
        client.branch.create(BRANCH, sync_with_git=False,
                             description="Typed signal predicates backfill")
    for sig in client.all("IntentObservabilitySignal", branch=BRANCH):
        name = sig.name.value
        if sig.operator.value:
            print(f"  = {name}: already typed ({sig.operator.value})")
            continue
        pred = signal_catalog.resolve_predicate(
            sig.signal.value,
            condition=sig.condition.value,
            frequency_seconds=sig.frequency_seconds.value or 30,
            name=name,
        )
        sig.operator.value = pred.operator
        sig.threshold.value = pred.threshold
        sig.threshold_ref.value = pred.threshold_ref
        sig.for_seconds.value = pred.for_seconds
        sig.save()
        print(f"  + {name}: {pred.operator} "
              f"{pred.threshold if pred.threshold_ref is None else 'ref:' + pred.threshold_ref}"
              f" for {pred.for_seconds}s")


if __name__ == "__main__":
    main()
