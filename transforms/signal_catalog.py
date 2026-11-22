"""Signal catalog - the agreement between what the intent compiles and what
the monitoring stack observes.

An ``IntentObservabilitySignal`` names a signal by id (``bgp_session_state``).
Everything downstream of that id lives HERE, once, instead of being
re-derived in every compiler:

    metric      canonical Prometheus metric name after normalization
                (telegraf.conf's starlark processor maps raw gNMI/CLI
                fields onto these names)
    labels      the join keys every collector MUST emit for this metric;
                intent labels (contract, tenant, ...) are NEVER emitted by
                collectors - they are joined in Prometheus from the
                compiled ``intent_session_info`` series
    scope       session (per BGP neighbor) | device (per box)
    threshold   which contract field bounds the metric, if any
    collection  per-collector, per-platform binding (gNMI paths today;
                cli_scrape is one script for every ssh_cli platform)
    facts_path  where the change-time gate validator finds the same fact in
                the executor's get_facts() output (ADR-0011)

Adding a vendor = adding a gnmi_paths entry. Adding a signal = adding one
SignalSpec + the schema dropdown choice. The intent model never changes.

Consumers: contract_expectations (evidence bindings), intent_series
(compiled info/threshold series), prometheus_rules (alert expressions),
telemetry_collector (subscriptions).
"""

from dataclasses import dataclass, field

CATALOG_VERSION = "v1"

# Every session-scoped metric is joined to intent on exactly these labels.
SESSION_JOIN = ("device", "neighbor")
DEVICE_JOIN = ("device",)

# The compiled series (see intent_series.py) - names are part of the catalog
# because rules and dashboards depend on them.
INFO_METRIC = "intent_session_info"
THRESHOLD_METRIC = "intent_threshold"


@dataclass(frozen=True)
class SignalSpec:
    id: str
    metric: str
    scope: str  # session | device
    description: str
    threshold: dict = field(default_factory=dict)  # afi -> contract field
    facts_path: str | None = None
    gnmi_paths: dict = field(default_factory=dict)  # platform -> [paths]
    cli_scrape: bool = False

    @property
    def join(self):
        return SESSION_JOIN if self.scope == "session" else DEVICE_JOIN


_OC_BGP = "/network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor"
_OC_STATE = [f"{_OC_BGP}/state/session-state"]
_OC_PREFIXES = [f"{_OC_BGP}/afi-safis/afi-safi/state/prefixes"]
_SRL_BGP = "/network-instance[name=default]/protocols/bgp/neighbor[peer-address=*]"

SIGNALS = {
    s.id: s
    for s in [
        SignalSpec(
            id="bgp_session_state",
            metric="bgp_session_state_established",
            scope="session",
            description="1 when the BGP session is Established, else 0",
            facts_path="bgp_neighbors.<vrf>.peers.<neighbor>.is_up",
            gnmi_paths={
                "arista_eos": _OC_STATE,
                "juniper_junos": _OC_STATE,
                "nokia_srlinux": [f"{_SRL_BGP}/session-state"],
            },
            cli_scrape=True,
        ),
        SignalSpec(
            id="bgp_route_count",
            metric="bgp_session_prefixes_received",
            scope="session",
            description="Prefixes received from the neighbor",
            threshold={"ipv4": "max_prefixes_ipv4", "ipv6": "max_prefixes_ipv6"},
            facts_path="bgp_neighbors.<vrf>.peers.<neighbor>.address_family.<afi>.received_prefixes",
            gnmi_paths={
                "arista_eos": _OC_PREFIXES,
                "juniper_junos": _OC_PREFIXES,
                "nokia_srlinux": [f"{_SRL_BGP}/afi-safi[afi-safi-name=*]/received-routes"],
            },
            cli_scrape=True,
        ),
        SignalSpec(
            # Honest limit: a sent-prefix COUNT bounds a leak, it does not
            # prove its absence. True leak evidence needs the advertised SET
            # (BMP / adj-rib-out) diffed against allowed_prefixes - owed.
            id="policy_export_violation",
            metric="bgp_session_prefixes_sent",
            scope="session",
            description="Prefixes advertised to the neighbor (count proxy for leaks)",
            threshold={"ipv4": "max_prefixes_ipv4", "ipv6": "max_prefixes_ipv6"},
            facts_path="bgp_neighbors.<vrf>.peers.<neighbor>.address_family.<afi>.sent_prefixes",
            gnmi_paths={
                "arista_eos": _OC_PREFIXES,
            },
            cli_scrape=True,
        ),
        SignalSpec(
            id="interface_errors",
            metric="interface_errors_total",
            scope="device",
            description="Interface error counters",
            gnmi_paths={
                "arista_eos": ["/interfaces/interface/state/counters"],
                "juniper_junos": ["/interfaces/interface/state/counters"],
                "nokia_srlinux": ["/interface[name=*]/statistics"],
            },
        ),
        SignalSpec(
            id="device_reachability",
            metric="ping_percent_packet_loss",
            scope="device",
            description="OOB management-plane packet loss (telegraf inputs.ping)",
        ),
    ]
}


def spec(signal_id):
    """Catalog lookup that fails loudly: an unknown signal id is a schema/
    catalog drift, and a silently skipped signal is an unwatched contract."""
    try:
        return SIGNALS[signal_id]
    except KeyError:
        raise ValueError(
            f"signal '{signal_id}' is not in signal catalog {CATALOG_VERSION}; "
            f"add a SignalSpec in transforms/signal_catalog.py"
        ) from None


def collector_for(capabilities):
    """Capability-driven collector selection (ADR-0017 D6 rule 3): the
    collector is derived from what the device type claims, never chosen."""
    if "gnmi" in capabilities:
        return "gnmi"
    if "snmp" in capabilities:
        return "snmp"
    if "ssh_cli" in capabilities:
        return "cli_scrape"
    return "unobservable"


def selector(labels):
    """PromQL label matcher from a dict, stable order."""
    inner = ",".join(f'{k}="{v}"' for k, v in labels.items() if v is not None)
    return "{" + inner + "}"


# --------------------------------------------------------------- predicates
# An ObservabilitySignal's alert condition is a typed predicate:
#   <catalog metric> <operator> <threshold | contract term>  for <for_seconds>
# The legacy free-text ``condition`` is parsed only when ``operator`` is unset,
# so unmigrated data keeps compiling; new data never needs the parser.

PROMQL_OPS = {"gt": ">", "ge": ">=", "lt": "<", "le": "<=", "eq": "==", "ne": "!="}

# contract terms a predicate may reference -> intent_threshold{term=...}
THRESHOLD_TERMS = {"max_prefixes"}


@dataclass(frozen=True)
class Predicate:
    operator: str
    threshold: float | None
    threshold_ref: str | None
    for_seconds: int
    source: str  # typed | legacy

    @property
    def promql_op(self):
        return PROMQL_OPS[self.operator]

    def as_dict(self):
        return {
            "operator": self.operator,
            "threshold": self.threshold,
            "thresholdRef": self.threshold_ref,
            "forSeconds": self.for_seconds,
            "source": self.source,
        }


def _num(x):
    return int(x) if float(x).is_integer() else float(x)


def resolve_predicate(signal_id, *, operator=None, threshold=None,
                      threshold_ref=None, for_seconds=None, condition=None,
                      frequency_seconds=30, name=None):
    """Typed fields -> Predicate; raises ValueError on anything ambiguous.
    The same rules back the merge check (checks/signal_predicate.py), so a
    predicate that merges is a predicate that compiles."""
    sig = spec(signal_id)
    label = name or signal_id
    if operator:
        if operator not in PROMQL_OPS:
            raise ValueError(f"signal '{label}': unknown operator '{operator}'")
        if (threshold is None) == (threshold_ref is None):
            raise ValueError(
                f"signal '{label}': set exactly one of threshold / threshold_ref")
        if threshold_ref is not None:
            if threshold_ref not in THRESHOLD_TERMS:
                raise ValueError(
                    f"signal '{label}': unknown threshold_ref '{threshold_ref}'")
            if not sig.threshold:
                raise ValueError(
                    f"signal '{label}': {signal_id} has no contract-bounded "
                    f"metric; use a literal threshold")
        return Predicate(operator, None if threshold is None else _num(threshold),
                         threshold_ref, int(for_seconds or 0), "typed")
    return _legacy_predicate(sig, condition, frequency_seconds, label)


def _legacy_predicate(sig, condition, frequency_seconds, label):
    import re

    text = condition or ""
    if m := re.search(r"down_for\s*>\s*(\d+)s", text):
        return Predicate("eq", 0, None, int(m.group(1)), "legacy")
    if "max_prefixes" in text and sig.threshold:
        return Predicate("gt", None, "max_prefixes", 0, "legacy")
    if m := re.search(r"count\s*>\s*(\d+)", text):
        return Predicate("gt", int(m.group(1)), None, 0, "legacy")
    if m := re.search(r"loss\s*>\s*(\d+)\s*%", text):
        return Predicate("gt", int(m.group(1)), None, 2 * frequency_seconds, "legacy")
    raise ValueError(
        f"signal '{label}': no typed predicate (operator unset) and legacy "
        f"condition '{condition}' is not recognized")
