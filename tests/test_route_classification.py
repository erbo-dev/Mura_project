"""Every route must declare which credential guards it.

Two jobs. First, prove the classification registry matches the live API surface,
so a new endpoint cannot ship without an auth class. Second, pin the current
CORE_API_KEY wiring: PR-03B-PREP must leave existing USER_APP routes exactly as
they were, and PR-03B-SWITCH must move all of them at once. Both directions are
asserted here, so neither a silent partial migration nor a forgotten route can
pass unnoticed.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app, get_settings
from mura.config import CoreSettings
from mura.identity.route_classification import (
    PENDING_SWITCH_TO_PRINCIPAL,
    PRINCIPAL_NATIVE,
    ROUTE_AUTH_CLASSES,
    AuthClass,
    classify,
    routes_in,
)


def _settings(**overrides: Any) -> CoreSettings:
    payload: dict[str, Any] = {
        "DEEPSEEK_API_KEY": "sk-" + "d" * 40,
        "CORE_API_KEY": "c" * 40,
        "OPERATIONS_API_KEY": "o" * 40,
        "WORKER_REGISTRATION_TOKEN": "r" * 40,
        "KAGGLE_ASR_API_KEY": "a" * 40,
        "DATABASE_URL": "sqlite+pysqlite:///:memory:",
    }
    payload.update(overrides)
    return CoreSettings.model_validate(payload)


#: Registered on the module-level APIRouter; pinned by behaviour, not wiring.
_BEHAVIOURALLY_PINNED = frozenset({"/v1/capabilities"})


def _live_paths() -> set[str]:
    return set(create_app(_settings()).openapi()["paths"])


def _dependency_names(dependant: Any) -> set[str]:
    """Walk the whole dependency tree.

    Router-level dependencies nest differently from decorator-level ones, so a
    shallow scan silently misses routes and would let a migrated route look
    unmigrated. Recursing is what makes this assertion trustworthy.
    """

    names: set[str] = set()
    for sub in getattr(dependant, "dependencies", []):
        call = getattr(sub, "call", None)
        if call is not None:
            names.add(getattr(call, "__name__", ""))
        names |= _dependency_names(sub)
    return names


def _paths_requiring(dependency_name: str) -> set[str]:
    application = create_app(_settings())
    found: set[str] = set()
    for route in application.routes:
        path = getattr(route, "path", None)
        dependant = getattr(route, "dependant", None)
        if path is None or dependant is None:
            continue
        if dependency_name in _dependency_names(dependant):
            found.add(path)
    return found


def test_every_live_route_has_exactly_one_auth_class() -> None:
    unclassified = sorted(path for path in _live_paths() if classify(path) is None)

    assert unclassified == [], "every route must declare an auth class in route_classification.py"


def test_the_registry_does_not_describe_routes_that_no_longer_exist() -> None:
    stale = sorted(set(ROUTE_AUTH_CLASSES) - _live_paths())

    assert stale == []


def test_expected_routes_land_in_expected_classes() -> None:
    assert routes_in(AuthClass.PUBLIC_INFRA) == {"/health", "/ready"}
    assert routes_in(AuthClass.WORKER) == {"/v1/workers/register", "/v1/workers/current"}
    assert routes_in(AuthClass.OPERATIONS) == {
        "/v1/operations/release",
        "/v1/operations/release/activate",
        "/v1/operations/release/rollback",
        "/v1/operations/retention",
        "/v1/operations/retention/apply",
    }
    assert routes_in(AuthClass.SERVICE_INTERNAL) == {
        "/v1/process-transcript",
        "/v1/jobs/{job_id}/trace",
        "/v1/families/{family_id}/replays",
    }


def test_replay_is_service_internal_not_family_data() -> None:
    # Deterministic replay is release/evaluation tooling. Family membership must
    # never grant it, so it is deliberately not USER_APP.
    assert classify("/v1/families/{family_id}/replays") is AuthClass.SERVICE_INTERNAL


def test_user_app_routes_are_either_principal_native_or_pending_switch() -> None:
    user_app = routes_in(AuthClass.USER_APP)

    assert PRINCIPAL_NATIVE | PENDING_SWITCH_TO_PRINCIPAL == user_app
    # No route may be in both states.
    assert not (PRINCIPAL_NATIVE & PENDING_SWITCH_TO_PRINCIPAL)


# ------------------------------------------------- PREP must not switch anything


def test_prep_left_existing_user_app_routes_on_core_api_key() -> None:
    """The atomic-switch guarantee, asserted mechanically.

    PR-03B-PREP adds machinery but must not migrate a single existing route. If
    one of these ever stops requiring CORE_API_KEY without the whole set moving,
    the repository has a partially enforced authorization layer.
    """

    core_dependency_paths = _paths_requiring("require_core_token")
    # Routes registered on the module-level APIRouter are not introspectable the
    # same way, so they are pinned behaviourally below instead.
    introspectable = PENDING_SWITCH_TO_PRINCIPAL - _BEHAVIOURALLY_PINNED

    still_on_core = introspectable & core_dependency_paths
    assert still_on_core == introspectable, (
        "PR-03B-PREP must not migrate existing USER_APP routes; "
        f"already migrated: {sorted(introspectable - core_dependency_paths)}"
    )


def test_capabilities_still_requires_the_service_token() -> None:
    """Behavioural pin for the router-registered pending route."""

    settings = _settings(DATABASE_AUTO_CREATE=True)
    application = create_app(settings)
    application.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(application, raise_server_exceptions=False)

    assert client.get("/v1/capabilities").status_code == 401
    assert (
        client.get("/v1/capabilities", headers={"Authorization": f"Bearer {'c' * 40}"}).status_code
        == 200
    )


def test_principal_native_routes_never_accept_the_service_token() -> None:
    leaked = PRINCIPAL_NATIVE & _paths_requiring("require_core_token")

    assert leaked == set(), (
        f"Principal-native routes must not accept CORE_API_KEY: {sorted(leaked)}"
    )


@pytest.mark.parametrize("path", sorted(PENDING_SWITCH_TO_PRINCIPAL))
def test_switch_checklist_entries_are_real_user_app_routes(path: str) -> None:
    assert classify(path) is AuthClass.USER_APP
