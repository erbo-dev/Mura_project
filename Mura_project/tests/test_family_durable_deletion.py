from __future__ import annotations

import pytest

from mura.identity.auth import VerifiedIdentity
from mura.storage.cleanup import StorageCleanupJobRow, StorageCleanupRepository
from mura.storage.database import Database
from mura.storage.identity import IdentityRepository


def test_family_delete_rolls_back_when_cleanup_enqueue_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = Database("sqlite+pysqlite:///:memory:")
    db.create_schema()
    identity = IdentityRepository(db)
    principal = identity.resolve_principal(
        VerifiedIdentity(
            issuer="https://auth.mura.test",
            subject="family-delete-rollback",
            email=None,
            display_name=None,
        )
    )
    family = identity.create_family(name="Rollback family", owner_user_id=principal.user_id)
    cleanup = StorageCleanupRepository(db)
    monkeypatch.setattr(
        cleanup,
        "enqueue_many",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("enqueue failed")),
    )

    with pytest.raises(RuntimeError, match="enqueue failed"):
        identity.delete_family(
            family.family_id,
            requesting_user_id=principal.user_id,
            cleanup_repository=cleanup,
        )

    assert (
        identity.get_family_for_member(
            family_id=family.family_id,
            user_id=principal.user_id,
        )
        is not None
    )
    with db.session_factory() as session:
        assert session.query(StorageCleanupJobRow).count() == 0
