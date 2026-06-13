"""Parity tests across the SCATTERED per-carrier knowledge.

Today the set of carriers and their per-carrier metadata is duplicated across
several modules with nothing enforcing they agree:

  * ``fretio.providers.factory._PROVIDER_SPECS``   (module/class/builder registry)
  * ``fretio.providers.factory._REQUIRED_FIELDS``  (required credential fields)
  * ``remote_permissions.KNOWN_CARRIERS``          (ordered carrier list)
  * ``cotacao.common.KNOWN_CARRIERS``              (re-export / fallback copy)
  * ``web_app._CARRIER_FIELDS``                    (credential UI fields)
  * ``cotacao.session_manager._PRIORIDADE_LENTIDAO`` (slowness priority)

These tests pin that all sources cover exactly the same carriers, in the same
canonical order, and that every required field is surfaced in the credential UI.
They are the safety net ("test needed first") for the Phase 6 step 2 work that
centralizes this knowledge into a single registry: any later slice that lets the
sources drift apart must fail here.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app" / "fretio" / "src"))

from fretio.providers.factory import (
    _PROVIDER_SPECS,
    _REQUIRED_FIELDS,
    credential_fields_for_provider,
    slowness_priority_for_provider,
)
from remote_permissions import KNOWN_CARRIERS
from cotacao.common import KNOWN_CARRIERS as COMMON_KNOWN_CARRIERS
from cotacao.session_manager import _PRIORIDADE_LENTIDAO
from web_app import _CARRIER_FIELDS


# The canonical carrier list and order is the provider-spec registry.
CANONICAL = tuple(_PROVIDER_SPECS.keys())


def test_canonical_carrier_set_is_the_expected_eight():
    assert CANONICAL == (
        "braspress",
        "trd",
        "agex",
        "eucatur",
        "rodonaves",
        "alfa",
        "coopex",
        "translovato",
    )


def test_known_carriers_matches_provider_specs_order():
    # KNOWN_CARRIERS (remote_permissions) drives licensing/usage; it must list
    # exactly the registered providers, in the same order.
    assert tuple(KNOWN_CARRIERS) == CANONICAL


def test_common_known_carriers_agrees_with_remote_permissions():
    assert tuple(COMMON_KNOWN_CARRIERS) == tuple(KNOWN_CARRIERS)


def test_required_fields_cover_exactly_the_canonical_carriers():
    assert set(_REQUIRED_FIELDS) == set(CANONICAL)


def test_credential_ui_fields_cover_exactly_the_canonical_carriers():
    assert set(_CARRIER_FIELDS) == set(CANONICAL)


def test_slowness_priority_covers_exactly_the_canonical_carriers():
    assert {k.lower() for k in _PRIORIDADE_LENTIDAO} == set(CANONICAL)


def test_every_required_field_is_surfaced_in_the_credential_ui():
    # A carrier whose required field is not present in the UI would be
    # impossible to configure — pin that they always line up.
    for carrier in CANONICAL:
        required = set(_REQUIRED_FIELDS[carrier])
        ui_keys = {key for key, _label, _type in _CARRIER_FIELDS[carrier]}
        missing = required - ui_keys
        assert not missing, f"{carrier}: required fields {missing} missing from credential UI"


def test_registry_credential_fields_faithfully_match_web_app():
    # The single registry must hold exactly the values web_app surfaces today,
    # so wiring web_app to read the registry is a pure no-op.
    for carrier in CANONICAL:
        assert tuple(_CARRIER_FIELDS[carrier]) == credential_fields_for_provider(carrier)


def test_registry_slowness_priority_faithfully_matches_session_manager():
    for carrier in CANONICAL:
        assert slowness_priority_for_provider(carrier) == _PRIORIDADE_LENTIDAO[carrier.upper()]
