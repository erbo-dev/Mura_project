"""PostgreSQL concurrency tests for family invitations.

SQLite cannot enforce FOR UPDATE row locks or multi-connection concurrent transactions,
so these tests validate row-level serialization, race handling, and convergence
against a real PostgreSQL instance.

Enable with:
    $env:TEST_POSTGRES_URL = 'postgresql+psycopg://mura_test@127.0.0.1:5432/mura_leases_test'
"""

from __future__ import annotations

import concurrent.futures
import os
from collections.abc import Iterator

import pytest
from sqlalchemy import select

from mura.identity.auth import Principal, VerifiedIdentity
from mura.identity.policy import FamilyRole
from mura.storage.database import Database
from mura.storage.identity import (
    FamilyInvitationRow,
    FamilyInvitationStatus,
    FamilyMembershipRow,
    FamilyRow,
    IdentityRepository,
    InvitationAlreadyAcceptedError,
    InvitationRevokedError,
    UserRow,
)

POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.skipif(
        not POSTGRES_URL,
        reason="TEST_POSTGRES_URL is required for the PostgreSQL invitation concurrency tests",
    ),
    pytest.mark.skipif(
        bool(POSTGRES_URL) and "test" not in (POSTGRES_URL or "").rsplit("/", 1)[-1].lower(),
        reason="TEST_POSTGRES_URL database name must contain 'test' to be treated as disposable",
    ),
]


def _purge(database: Database) -> None:
    with database.session_factory.begin() as session:
        session.query(FamilyInvitationRow).delete()
        session.query(FamilyMembershipRow).delete()
        session.query(FamilyRow).delete()
        session.query(UserRow).delete()


@pytest.fixture
def repository() -> Iterator[IdentityRepository]:
    assert POSTGRES_URL is not None
    database = Database(POSTGRES_URL)
    database.create_schema()
    _purge(database)
    try:
        yield IdentityRepository(database)
    finally:
        _purge(database)


def _create_verified_user(repo: IdentityRepository, subject: str, name: str) -> Principal:
    return repo.resolve_principal(
        VerifiedIdentity(
            issuer="https://accounts.google.com",
            subject=subject,
            email=f"{subject}@example.com",
            display_name=name,
        )
    )


def test_concurrent_same_user_accept_converges_idempotently(
    repository: IdentityRepository,
) -> None:
    """When a single user rapidly submits the accept form multiple times (or network retry),
    one request performs the initial acceptance and subsequent requests converge idempotently
    without errors or duplicate membership rows.
    """
    owner = _create_verified_user(repository, "owner_1", "Owner One")
    family = repository.create_family(owner_user_id=owner.user_id, name="Race Family 1")
    invitee = _create_verified_user(repository, "invitee_1", "Invitee One")

    invitation, raw_token = repository.create_invitation(
        family_id=family.family_id,
        creator_user_id=owner.user_id,
        intended_role=FamilyRole.EDITOR,
    )

    results: list[tuple[FamilyInvitationRow, FamilyMembershipRow, bool]] = []
    errors: list[Exception] = []

    def _worker_accept() -> None:
        try:
            res = repository.accept_invitation(
                token=raw_token,
                user_id=invitee.user_id,
            )
            results.append(res)
        except Exception as exc:
            errors.append(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(_worker_accept) for _ in range(5)]
        concurrent.futures.wait(futures)

    assert len(errors) == 0, f"Unexpected errors: {errors}"
    assert len(results) == 5

    # Exactly one execution should have performed the original acceptance (replayed=False)
    # and 4 should have converged as idempotent replays (replayed=True)
    replayed_flags = [r[2] for r in results]
    assert replayed_flags.count(False) == 1
    assert replayed_flags.count(True) == 4

    # Verify database state
    with repository.database.session_factory() as session:
        memberships = session.scalars(
            select(FamilyMembershipRow).where(
                FamilyMembershipRow.family_id == family.family_id,
                FamilyMembershipRow.user_id == invitee.user_id,
            )
        ).all()
        assert len(memberships) == 1
        assert memberships[0].role == FamilyRole.EDITOR.value

        inv_row = session.scalar(
            select(FamilyInvitationRow).where(
                FamilyInvitationRow.invitation_id == invitation.invitation_id
            )
        )
        assert inv_row is not None
        assert inv_row.status == FamilyInvitationStatus.ACCEPTED.value
        assert inv_row.accepted_by_user_id == invitee.user_id


def test_concurrent_different_users_single_winner(
    repository: IdentityRepository,
) -> None:
    """When multiple different users race to accept the exact same single-use invitation token,
    PostgreSQL FOR UPDATE row locks ensure exactly one user succeeds; all other users
    raise InvitationAlreadyAcceptedError.
    """
    owner = _create_verified_user(repository, "owner_2", "Owner Two")
    family = repository.create_family(owner_user_id=owner.user_id, name="Race Family 2")

    users = [_create_verified_user(repository, f"competitor_{i}", f"User {i}") for i in range(5)]

    invitation, raw_token = repository.create_invitation(
        family_id=family.family_id,
        creator_user_id=owner.user_id,
        intended_role=FamilyRole.VIEWER,
    )

    successes: list[str] = []
    already_accepted_errors: list[Exception] = []
    other_errors: list[Exception] = []

    def _worker_accept(user_id: str) -> None:
        try:
            repository.accept_invitation(token=raw_token, user_id=user_id)
            successes.append(user_id)
        except InvitationAlreadyAcceptedError as exc:
            already_accepted_errors.append(exc)
        except Exception as exc:
            other_errors.append(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(_worker_accept, u.user_id) for u in users]
        concurrent.futures.wait(futures)

    assert len(other_errors) == 0, f"Unexpected other errors: {other_errors}"
    assert len(successes) == 1, f"Expected exactly 1 winner, got: {successes}"
    assert len(already_accepted_errors) == 4

    winner_id = successes[0]

    # Verify DB state
    with repository.database.session_factory() as session:
        memberships = session.scalars(
            select(FamilyMembershipRow).where(FamilyMembershipRow.family_id == family.family_id)
        ).all()
        # Owner + exactly 1 winner = 2 memberships
        assert len(memberships) == 2
        member_user_ids = {m.user_id for m in memberships}
        assert winner_id in member_user_ids

        inv_row = session.scalar(
            select(FamilyInvitationRow).where(
                FamilyInvitationRow.invitation_id == invitation.invitation_id
            )
        )
        assert inv_row is not None
        assert inv_row.status == FamilyInvitationStatus.ACCEPTED.value
        assert inv_row.accepted_by_user_id == winner_id


def test_concurrent_accept_and_revoke_deterministic_outcome(
    repository: IdentityRepository,
) -> None:
    """When an accept and a revoke occur concurrently on the same invitation row,
    FOR UPDATE serialization guarantees:
    - Either accept commits first (invitation is ACCEPTED, revoke fails with RuntimeError)
    - Or revoke commits first (invitation is REVOKED, accept fails with InvitationRevokedError)
    Under NO circumstances can an invitation end up in an invalid state.
    """
    owner = _create_verified_user(repository, "owner_3", "Owner Three")
    family = repository.create_family(owner_user_id=owner.user_id, name="Race Family 3")
    invitee = _create_verified_user(repository, "invitee_3", "Invitee Three")

    invitation, raw_token = repository.create_invitation(
        family_id=family.family_id,
        creator_user_id=owner.user_id,
        intended_role=FamilyRole.EDITOR,
    )

    accept_result: list[bool] = []
    accept_error: list[Exception] = []
    revoke_result: list[bool] = []
    revoke_error: list[Exception] = []

    def _do_accept() -> None:
        try:
            repository.accept_invitation(token=raw_token, user_id=invitee.user_id)
            accept_result.append(True)
        except Exception as exc:
            accept_error.append(exc)

    def _do_revoke() -> None:
        try:
            repository.revoke_invitation(
                family_id=family.family_id,
                invitation_id=invitation.invitation_id,
                revoker_user_id=owner.user_id,
            )
            revoke_result.append(True)
        except Exception as exc:
            revoke_error.append(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(_do_accept)
        f2 = executor.submit(_do_revoke)
        concurrent.futures.wait([f1, f2])

    with repository.database.session_factory() as session:
        inv_row = session.scalar(
            select(FamilyInvitationRow).where(
                FamilyInvitationRow.invitation_id == invitation.invitation_id
            )
        )
        assert inv_row is not None

        if len(accept_result) == 1:
            # Accept won the race
            assert inv_row.status == FamilyInvitationStatus.ACCEPTED.value
            assert inv_row.accepted_by_user_id == invitee.user_id
            assert len(revoke_error) == 1
            assert "already accepted" in str(revoke_error[0]).lower()
        else:
            # Revoke won the race
            assert len(revoke_result) == 1
            assert inv_row.status == FamilyInvitationStatus.REVOKED.value
            assert len(accept_error) == 1
            assert isinstance(accept_error[0], InvitationRevokedError)
