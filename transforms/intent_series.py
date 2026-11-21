"""Intent compiler: intent series - the bridge between intent and telemetry.

Collectors emit RAW facts keyed by ``device`` + ``neighbor`` and know
nothing about contracts or tenants. This artifact publishes what the
intent EXPECTS as Prometheus series, so the meaning is joined at query
time instead of being baked into collectors:

    intent_session_info{contract, tenant, intent, role, device, neighbor, ...} 1
        one series per session the contract demands
    intent_threshold{contract, term, afi, bound} <value>
        one series per numeric contract term (max_prefixes today)

Alert rules then read as joins over the signal catalog's join keys:

    bgp_session_prefixes_received
      * on(device, neighbor) group_left(contract, tenant) intent_session_info
      > on(contract) group_left() intent_threshold{term="max_prefixes"}

and "expected but not observed" becomes expressible per session:

    intent_session_info unless on(device, neighbor) bgp_session_state_established

Consequences: a PE serving N contracts is labelled correctly per neighbor;
changing a threshold or adding a session changes series, never rules or
collectors; the gate validator and Prometheus share one expectation set.

Delivery: the series are emitted as Prometheus RECORDING rules
(``expr: vector(<value>)`` + static labels), so they ride the same
rule_files glob as the alert rules - no exporter, no extra scrape job.

Projection only: input is ``contract_expectations.build_expectations``,
the compiler IR. Targeted artifact, one file per ``routing_contracts`` member.
"""

import pathlib
import sys

import yaml

from infrahub_sdk.transforms import InfrahubTransform

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import signal_catalog  # noqa: E402
from contract_expectations import build_expectations  # noqa: E402


def _series(record, value, labels):
    return {
        "record": record,
        "expr": f"vector({value})",
        "labels": {k: str(v) for k, v in labels.items() if v is not None},
    }


def project_series(expectations):
    """ContractExpectations dict -> Prometheus recording-rule document."""
    meta = expectations["metadata"]
    bgp = expectations["spec"]["bgp"]
    contract = meta["name"]
    common = {
        "contract": contract,
        "tenant": meta.get("tenant") or "infrastructure",
        "intent": meta.get("intent"),
        "role": meta.get("role"),
        "managed_by": "infrahub-intent",
    }

    rules = []
    for session in bgp.get("sessions") or []:
        rules.append(_series(signal_catalog.INFO_METRIC, 1, {
            **common,
            "device": session["device"],
            "neighbor": session["neighborIp"],
            "neighbor_device": session.get("neighborDevice"),
            "observed_via": session.get("observedVia"),
            "peer_asn": session.get("peerAsn"),
        }))

    max_prefixes = (bgp.get("routePolicy") or {}).get("inbound", {}).get("maxPrefixes") or {}
    for afi, value in sorted(max_prefixes.items()):
        rules.append(_series(signal_catalog.THRESHOLD_METRIC, value, {
            "contract": contract,
            "term": "max_prefixes",
            "afi": afi,
            "bound": "max",
            "managed_by": "infrahub-intent",
        }))

    return {
        "groups": [{
            "name": f"intent-series-{contract}",
            "rules": rules,
        }]
    }


class IntentSeriesTransform(InfrahubTransform):
    query = "contract_expectations"

    async def transform(self, data):
        doc = project_series(build_expectations(data))
        header = (
            f"# Compiled by InfraHub from contract "
            f"{doc['groups'][0]['name'].removeprefix('intent-series-')} "
            f"(signal catalog {signal_catalog.CATALOG_VERSION}).\n"
            f"# Recording rules publish what the intent EXPECTS; alert "
            f"rules join raw telemetry against these series.\n"
        )
        return header + yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)
