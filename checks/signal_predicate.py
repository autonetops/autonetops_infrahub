"""Signal predicates must compile - checked at merge time.

Every IntentObservabilitySignal is an alert predicate over a catalog
metric: ``<metric> <operator> <threshold | contract term> for <for_seconds>``.
This check runs the SAME resolver the compilers use
(``signal_catalog.resolve_predicate``), so a predicate that merges is a
predicate that compiles. It rejects:

  - unknown signal ids (not in the signal catalog)
  - operator set with zero or two bounds (threshold AND threshold_ref)
  - threshold_ref on a signal whose metric has no contract bound
  - threshold_ref to a term the watched contract does not define
  - session-scoped signals that watch no contract
  - unmigrated text conditions the legacy parser cannot read

It reports (log_info, no block) signals still relying on the legacy text
condition, so migration to typed fields is visible.
"""

import pathlib
import sys

from infrahub_sdk.checks import InfrahubCheck

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "transforms"))
import signal_catalog  # noqa: E402


def _v(attr):
    return attr.get("value") if attr else None


class SignalPredicateCheck(InfrahubCheck):
    query = "signal_predicate"

    def validate(self, data):
        for edge in data["IntentObservabilitySignal"]["edges"]:
            s = edge["node"]
            name = _v(s["name"])
            watched = (s.get("watches") or {}).get("node")
            try:
                sig = signal_catalog.spec(_v(s["signal"]))
                pred = signal_catalog.resolve_predicate(
                    sig.id,
                    operator=_v(s.get("operator")),
                    threshold=_v(s.get("threshold")),
                    threshold_ref=_v(s.get("threshold_ref")),
                    for_seconds=_v(s.get("for_seconds")),
                    condition=_v(s.get("condition")),
                    frequency_seconds=_v(s.get("frequency_seconds")) or 30,
                    name=name,
                )
            except ValueError as exc:
                self.log_error(message=str(exc))
                continue

            if sig.scope == "session" and not watched:
                self.log_error(
                    message=f"signal '{name}': {sig.id} is per BGP session and "
                            f"must watch a routing contract")
            if pred.threshold_ref and watched:
                field = sig.threshold.get("ipv4")
                if "max_prefixes_ipv4" in watched and _v(watched.get(field)) is None:
                    self.log_error(
                        message=f"signal '{name}': threshold_ref "
                                f"{pred.threshold_ref} but contract "
                                f"'{_v(watched['name'])}' defines no {field}")
            if pred.source == "legacy":
                self.log_info(
                    message=f"signal '{name}': still compiled from legacy text "
                            f"condition '{_v(s.get('condition'))}' - set "
                            f"operator/threshold/threshold_ref/for_seconds")
