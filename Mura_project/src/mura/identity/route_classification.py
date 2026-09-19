"""Authoritative auth class for every API route.

Kept as data rather than a framework. Its job is to make an unclassified route
impossible to ship quietly: a test walks the live OpenAPI surface and fails if
anything is missing here, so a new endpoint must state which credential guards
it before it merges.
"""

from __future__ import annotations

from enum import StrEnum


class AuthClass(StrEnum):
    #: Unauthenticated liveness/readiness probes.
    PUBLIC_INFRA = "public_infra"
    #: End-user data. Requires a verified user Principal plus family membership.
    USER_APP = "user_app"
    #: Engineering/pipeline surfaces. CORE_API_KEY only, never a user token.
    SERVICE_INTERNAL = "service_internal"
    #: Destructive operator surfaces. OPERATIONS_API_KEY only.
    OPERATIONS = "operations"
    #: ASR worker transport. WORKER_REGISTRATION_TOKEN only.
    WORKER = "worker"


#: Path template -> auth class. Paths match FastAPI's OpenAPI keys exactly.
ROUTE_AUTH_CLASSES: dict[str, AuthClass] = {
    "/health": AuthClass.PUBLIC_INFRA,
    "/ready": AuthClass.PUBLIC_INFRA,
    # Capabilities gates the record button, so it is user-facing product data.
    "/v1/capabilities": AuthClass.USER_APP,
    "/v1/me": AuthClass.USER_APP,
    "/v1/families": AuthClass.USER_APP,
    "/v1/families/{family_id}": AuthClass.USER_APP,
    "/v1/families/{family_id}/members": AuthClass.USER_APP,
    "/v1/families/{family_id}/members/{user_id}": AuthClass.USER_APP,
    "/v1/families/{family_id}/recordings": AuthClass.USER_APP,
    "/v1/families/{family_id}/recordings/{recording_id}": AuthClass.USER_APP,
    "/v1/families/{family_id}/recordings/{recording_id}/review-items": AuthClass.USER_APP,
    "/v1/families/{family_id}/jobs/{job_id}": AuthClass.USER_APP,
    "/v1/families/{family_id}/archive": AuthClass.USER_APP,
    "/v1/families/{family_id}/people": AuthClass.USER_APP,
    "/v1/families/{family_id}/relationships": AuthClass.USER_APP,
    "/v1/families/{family_id}/stories": AuthClass.USER_APP,
    "/v1/families/{family_id}/stories/{story_id}": AuthClass.USER_APP,
    "/v1/families/{family_id}/review-items": AuthClass.USER_APP,
    "/v1/families/{family_id}/recordings/{recording_id}/audio": AuthClass.USER_APP,
    "/v1/families/{family_id}/profiles": AuthClass.USER_APP,
    "/v1/families/{family_id}/profiles/{person_id}": AuthClass.USER_APP,
    "/v1/families/{family_id}/conflicts": AuthClass.USER_APP,
    "/v1/families/{family_id}/conflicts/{conflict_id}": AuthClass.USER_APP,
    "/v1/families/{family_id}/conflicts/{conflict_id}/resolve": AuthClass.USER_APP,
    "/v1/families/{family_id}/conflicts/{conflict_id}/dismiss": AuthClass.USER_APP,
    "/v1/families/{family_id}/conflicts/{conflict_id}/reopen": AuthClass.USER_APP,
    "/v1/families/{family_id}/books": AuthClass.USER_APP,
    "/v1/families/{family_id}/books/sources": AuthClass.USER_APP,
    "/v1/families/{family_id}/books/{book_id}": AuthClass.USER_APP,
    "/v1/families/{family_id}/books/{book_id}/status": AuthClass.USER_APP,
    "/v1/families/{family_id}/books/{book_id}/chapters": AuthClass.USER_APP,
    "/v1/families/{family_id}/books/{book_id}/chapters/{chapter_number}": AuthClass.USER_APP,
    "/v1/families/{family_id}/books/{book_id}/download": AuthClass.USER_APP,
    "/v1/families/{family_id}/books/{book_id}/cancel": AuthClass.USER_APP,
    "/v1/families/{family_id}/books/{book_id}/regenerate": AuthClass.USER_APP,
    "/v1/families/{family_id}/privacy/export": AuthClass.USER_APP,
    # Deterministic replay is release/evaluation tooling reached only by the
    # smoke gate and evaluation tests. Family membership must not grant it.
    "/v1/families/{family_id}/replays": AuthClass.SERVICE_INTERNAL,
    # Operational stages, timings and attempt metadata; no product surface reads it.
    "/v1/jobs/{job_id}/trace": AuthClass.SERVICE_INTERNAL,
    # Pipeline surface with no family scope at all.
    "/v1/process-transcript": AuthClass.SERVICE_INTERNAL,
    "/v1/operations/release": AuthClass.OPERATIONS,
    "/v1/operations/release/activate": AuthClass.OPERATIONS,
    "/v1/operations/release/rollback": AuthClass.OPERATIONS,
    "/v1/operations/retention": AuthClass.OPERATIONS,
    "/v1/operations/retention/apply": AuthClass.OPERATIONS,
    "/v1/operations/monitoring/summary": AuthClass.OPERATIONS,
    "/v1/workers/register": AuthClass.WORKER,
    "/v1/workers/current": AuthClass.WORKER,
}


#: USER_APP routes still guarded by CORE_API_KEY.
#:
#: PR-03B-SWITCH emptied this set: every family application route now resolves a
#: verified Principal and a family membership. It stays here, empty, because the
#: accompanying test asserts emptiness -- reintroducing a service-token route on
#: the user surface has to be a deliberate, visible act rather than an omission.
PENDING_SWITCH_TO_PRINCIPAL: frozenset[str] = frozenset()


#: USER_APP routes on the Principal chain. After the switch this is all of them.
PRINCIPAL_NATIVE: frozenset[str] = frozenset(
    {
        "/v1/capabilities",
        "/v1/me",
        "/v1/families",
        "/v1/families/{family_id}",
        "/v1/families/{family_id}/members",
        "/v1/families/{family_id}/members/{user_id}",
        "/v1/families/{family_id}/recordings",
        "/v1/families/{family_id}/recordings/{recording_id}",
        "/v1/families/{family_id}/recordings/{recording_id}/review-items",
        "/v1/families/{family_id}/jobs/{job_id}",
        "/v1/families/{family_id}/archive",
        "/v1/families/{family_id}/people",
        "/v1/families/{family_id}/relationships",
        "/v1/families/{family_id}/stories",
        "/v1/families/{family_id}/stories/{story_id}",
        "/v1/families/{family_id}/review-items",
        "/v1/families/{family_id}/recordings/{recording_id}/audio",
        "/v1/families/{family_id}/profiles",
        "/v1/families/{family_id}/profiles/{person_id}",
        "/v1/families/{family_id}/conflicts",
        "/v1/families/{family_id}/conflicts/{conflict_id}",
        "/v1/families/{family_id}/conflicts/{conflict_id}/resolve",
        "/v1/families/{family_id}/conflicts/{conflict_id}/dismiss",
        "/v1/families/{family_id}/conflicts/{conflict_id}/reopen",
        "/v1/families/{family_id}/books",
        "/v1/families/{family_id}/books/sources",
        "/v1/families/{family_id}/books/{book_id}",
        "/v1/families/{family_id}/books/{book_id}/status",
        "/v1/families/{family_id}/books/{book_id}/chapters",
        "/v1/families/{family_id}/books/{book_id}/chapters/{chapter_number}",
        "/v1/families/{family_id}/books/{book_id}/download",
        "/v1/families/{family_id}/books/{book_id}/cancel",
        "/v1/families/{family_id}/books/{book_id}/regenerate",
        "/v1/families/{family_id}/privacy/export",
    }
)


#: The capability each family-scoped USER_APP route requires. Routes that need
#: only a verified user (no family scope) are absent on purpose.
CAPABILITY_BY_ROUTE: dict[str, str] = {
    "/v1/families/{family_id}": "read_family",
    "/v1/families/{family_id}/members": "read_members",
    "/v1/families/{family_id}/members/{user_id}": "manage_members",
    "/v1/families/{family_id}/recordings": "create_recording",
    "/v1/families/{family_id}/recordings/{recording_id}": "read_recordings",
    "/v1/families/{family_id}/recordings/{recording_id}/review-items": "read_review",
    "/v1/families/{family_id}/jobs/{job_id}": "read_jobs",
    "/v1/families/{family_id}/archive": "read_family",
    "/v1/families/{family_id}/people": "read_profiles",
    "/v1/families/{family_id}/relationships": "read_profiles",
    "/v1/families/{family_id}/stories": "read_recordings",
    "/v1/families/{family_id}/stories/{story_id}": "read_recordings",
    "/v1/families/{family_id}/review-items": "read_review",
    "/v1/families/{family_id}/recordings/{recording_id}/audio": "read_recordings",
    "/v1/families/{family_id}/profiles": "read_profiles",
    "/v1/families/{family_id}/profiles/{person_id}": "read_profiles",
    "/v1/families/{family_id}/conflicts": "read_conflicts",
    "/v1/families/{family_id}/conflicts/{conflict_id}": "read_conflicts",
    "/v1/families/{family_id}/conflicts/{conflict_id}/resolve": "resolve_conflicts",
    "/v1/families/{family_id}/conflicts/{conflict_id}/dismiss": "resolve_conflicts",
    "/v1/families/{family_id}/conflicts/{conflict_id}/reopen": "resolve_conflicts",
    "/v1/families/{family_id}/books": "create_book",
    "/v1/families/{family_id}/books/sources": "read_books",
    "/v1/families/{family_id}/books/{book_id}": "read_books",
    "/v1/families/{family_id}/books/{book_id}/status": "read_books",
    "/v1/families/{family_id}/books/{book_id}/chapters": "read_books",
    "/v1/families/{family_id}/books/{book_id}/chapters/{chapter_number}": "read_books",
    "/v1/families/{family_id}/books/{book_id}/download": "read_books",
    "/v1/families/{family_id}/books/{book_id}/cancel": "create_book",
    "/v1/families/{family_id}/books/{book_id}/regenerate": "create_book",
    "/v1/families/{family_id}/privacy/export": "read_family",
}


def classify(path: str) -> AuthClass | None:
    return ROUTE_AUTH_CLASSES.get(path)


def routes_in(auth_class: AuthClass) -> frozenset[str]:
    return frozenset(path for path, value in ROUTE_AUTH_CLASSES.items() if value is auth_class)
