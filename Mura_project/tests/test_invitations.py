from __future__ import annotations

import hashlib
import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.api.errors import (
    AUTHENTICATION_REQUIRED,
    INSUFFICIENT_FAMILY_ROLE,
    INVALID_INVITATION_ROLE,
    INVITATION_ALREADY_ACCEPTED,
    INVITATION_EXPIRED,
    INVITATION_NOT_FOUND,
    INVITATION_REVOKED,
)
from apps.api.main import (
    create_app,
    get_auth_verifier,
    get_runtime,
    get_settings,
)
from mura.config import CoreSettings
from mura.identity.policy import FamilyRole
from mura.storage.database import Database
from mura.storage.identity import (
    FamilyInvitationRow,
    FamilyMembershipRow,
    IdentityRepository,
)
from tests.authz_factories import (
    FakePrincipalVerifier,
    create_membership,
    create_test_family,
    create_test_user,
)

CORE_TOKEN = "c" * 40
OPERATIONS_TOKEN = "o" * 40
WORKER_TOKEN = "r" * 40


def _settings(**overrides: Any) -> CoreSettings:
    payload: dict[str, Any] = {
        "DEEPSEEK_API_KEY": "sk-" + "d" * 40,
        "CORE_API_KEY": CORE_TOKEN,
        "OPERATIONS_API_KEY": OPERATIONS_TOKEN,
        "WORKER_REGISTRATION_TOKEN": WORKER_TOKEN,
        "KAGGLE_ASR_API_KEY": "a" * 40,
        "DATABASE_URL": "sqlite+pysqlite:///:memory:",
        "DATABASE_AUTO_CREATE": True,
    }
    payload.update(overrides)
    return CoreSettings(**payload)


@pytest.fixture
def auth_fixture() -> tuple[TestClient, Database, IdentityRepository, FakePrincipalVerifier]:
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_schema()
    repository = IdentityRepository(database)
    verifier = FakePrincipalVerifier()
    app = create_app(settings=_settings())

    class _RuntimeShim:
        def __init__(self, db: Database, repo: IdentityRepository) -> None:
            self.database = db
            self.identity_repository = repo
            self.settings = _settings()

    shim = _RuntimeShim(database, repository)
    app.dependency_overrides[get_runtime] = lambda: shim
    app.dependency_overrides[get_settings] = lambda: shim.settings
    app.dependency_overrides[get_auth_verifier] = lambda: verifier
    client = TestClient(app)
    return client, database, repository, verifier


def test_owner_can_create_invitation(
    auth_fixture: tuple[TestClient, Database, IdentityRepository, FakePrincipalVerifier],
) -> None:
    client, _, repository, verifier = auth_fixture
    owner = create_test_user(
        repository, subject="owner_alice", display_name="Alice", verifier=verifier
    )
    family_id = create_test_family(repository, name="The Shaimardanovs", owner=owner)

    # 1. Create Editor Invite
    resp = client.post(
        f"/v1/families/{family_id}/invitations",
        json={"role": "editor"},
        headers=owner.headers,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["family_id"] == family_id
    assert data["intended_role"] == "editor"
    assert data["status"] == "pending"
    assert "invitation_url" in data
    assert data["invitation_url"].startswith("/invite/")
    token = data["invitation_url"].split("/invite/")[1]
    assert len(token) > 20

    # 2. Create Viewer Invite
    resp_viewer = client.post(
        f"/v1/families/{family_id}/invitations",
        json={"role": "viewer"},
        headers=owner.headers,
    )
    assert resp_viewer.status_code == 201
    assert resp_viewer.json()["intended_role"] == "viewer"


def test_viewer_and_editor_cannot_create_invitation(
    auth_fixture: tuple[TestClient, Database, IdentityRepository, FakePrincipalVerifier],
) -> None:
    client, database, repository, verifier = auth_fixture
    owner = create_test_user(repository, subject="owner_1", verifier=verifier)
    editor = create_test_user(repository, subject="editor_1", verifier=verifier)
    viewer = create_test_user(repository, subject="viewer_1", verifier=verifier)
    family_id = create_test_family(repository, name="Family 1", owner=owner)
    create_membership(database, family_id=family_id, user=editor, role=FamilyRole.EDITOR)
    create_membership(database, family_id=family_id, user=viewer, role=FamilyRole.VIEWER)

    resp_editor = client.post(
        f"/v1/families/{family_id}/invitations",
        json={"role": "viewer"},
        headers=editor.headers,
    )
    assert resp_editor.status_code == 403
    assert resp_editor.json()["error"]["code"] == INSUFFICIENT_FAMILY_ROLE

    resp_viewer = client.post(
        f"/v1/families/{family_id}/invitations",
        json={"role": "viewer"},
        headers=viewer.headers,
    )
    assert resp_viewer.status_code == 403
    assert resp_viewer.json()["error"]["code"] == INSUFFICIENT_FAMILY_ROLE


def test_unauthenticated_cannot_create_invitation(
    auth_fixture: tuple[TestClient, Database, IdentityRepository, FakePrincipalVerifier],
) -> None:
    client, _, repository, verifier = auth_fixture
    owner = create_test_user(repository, subject="owner_2", verifier=verifier)
    family_id = create_test_family(repository, name="Family 2", owner=owner)

    resp = client.post(
        f"/v1/families/{family_id}/invitations",
        json={"role": "editor"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == AUTHENTICATION_REQUIRED


def test_cannot_invite_with_owner_role(
    auth_fixture: tuple[TestClient, Database, IdentityRepository, FakePrincipalVerifier],
) -> None:
    client, _, repository, verifier = auth_fixture
    owner = create_test_user(repository, subject="owner_3", verifier=verifier)
    family_id = create_test_family(repository, name="Family 3", owner=owner)

    resp = client.post(
        f"/v1/families/{family_id}/invitations",
        json={"role": "owner"},
        headers=owner.headers,
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == INVALID_INVITATION_ROLE


def test_token_is_never_stored_plaintext(
    auth_fixture: tuple[TestClient, Database, IdentityRepository, FakePrincipalVerifier],
) -> None:
    client, database, repository, verifier = auth_fixture
    owner = create_test_user(repository, subject="owner_sec", verifier=verifier)
    family_id = create_test_family(repository, name="Family Sec", owner=owner)

    resp = client.post(
        f"/v1/families/{family_id}/invitations",
        json={"role": "editor"},
        headers=owner.headers,
    )
    assert resp.status_code == 201
    token = resp.json()["invitation_url"].split("/invite/")[1]
    expected_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    with database.session_factory() as session:
        invitation = session.get(FamilyInvitationRow, resp.json()["invitation_id"])
        assert invitation is not None
        assert invitation.token_hash == expected_hash
        assert token not in repr(invitation)
        assert token != invitation.token_hash


def test_preview_invitation_unauthenticated(
    auth_fixture: tuple[TestClient, Database, IdentityRepository, FakePrincipalVerifier],
) -> None:
    client, _, repository, verifier = auth_fixture
    owner = create_test_user(
        repository, subject="owner_prev", display_name="Aisulu", verifier=verifier
    )
    family_id = create_test_family(repository, name="The Shaimardanovs", owner=owner)

    create_resp = client.post(
        f"/v1/families/{family_id}/invitations",
        json={"role": "editor"},
        headers=owner.headers,
    )
    token = create_resp.json()["invitation_url"].split("/invite/")[1]

    # Public preview without headers
    preview_resp = client.get(f"/v1/invitations/{token}/preview")
    assert preview_resp.status_code == 200
    pdata = preview_resp.json()
    assert pdata["family_name"] == "The Shaimardanovs"
    assert pdata["inviter_name"] == "Aisulu"
    assert pdata["intended_role"] == "editor"
    assert pdata["status"] == "pending"

    # Nonexistent token
    fake_resp = client.get("/v1/invitations/nonexistent_token_12345/preview")
    assert fake_resp.status_code == 404
    assert fake_resp.json()["error"]["code"] == INVITATION_NOT_FOUND


def test_accept_invitation_authenticated(
    auth_fixture: tuple[TestClient, Database, IdentityRepository, FakePrincipalVerifier],
) -> None:
    client, database, repository, verifier = auth_fixture
    owner = create_test_user(repository, subject="owner_acc", verifier=verifier)
    family_id = create_test_family(repository, name="Family Acc", owner=owner)

    create_resp = client.post(
        f"/v1/families/{family_id}/invitations",
        json={"role": "editor"},
        headers=owner.headers,
    )
    token = create_resp.json()["invitation_url"].split("/invite/")[1]

    # Recipient authenticates
    recipient = create_test_user(
        repository, subject="recipient_bob", display_name="Bob", verifier=verifier
    )

    accept_resp = client.post(
        f"/v1/invitations/{token}/accept",
        headers=recipient.headers,
    )
    assert accept_resp.status_code == 200, accept_resp.text
    adata = accept_resp.json()
    assert adata["family_id"] == family_id
    assert adata["role"] == "editor"
    assert adata["already_member"] is False

    # Check database membership
    with database.session_factory() as session:
        membership = session.get(FamilyMembershipRow, adata["membership_id"])
        assert membership is not None
        assert membership.user_id == recipient.user_id
        assert membership.family_id == family_id
        assert membership.role == "editor"

        invitation = session.get(FamilyInvitationRow, create_resp.json()["invitation_id"])
        assert invitation is not None
        assert invitation.status == "accepted"
        assert invitation.accepted_by_user_id == recipient.user_id
        assert invitation.accepted_at is not None


def test_accept_invitation_unauthenticated_fails(
    auth_fixture: tuple[TestClient, Database, IdentityRepository, FakePrincipalVerifier],
) -> None:
    client, _, repository, verifier = auth_fixture
    owner = create_test_user(repository, subject="owner_unauth", verifier=verifier)
    family_id = create_test_family(repository, name="Family Unauth", owner=owner)

    create_resp = client.post(
        f"/v1/families/{family_id}/invitations",
        json={"role": "editor"},
        headers=owner.headers,
    )
    token = create_resp.json()["invitation_url"].split("/invite/")[1]

    resp = client.post(f"/v1/invitations/{token}/accept")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == AUTHENTICATION_REQUIRED


def test_expired_invitation_acceptance_fails(
    auth_fixture: tuple[TestClient, Database, IdentityRepository, FakePrincipalVerifier],
) -> None:
    client, _, repository, verifier = auth_fixture
    owner = create_test_user(repository, subject="owner_exp", verifier=verifier)
    family_id = create_test_family(repository, name="Family Exp", owner=owner)

    # Create invitation with negative TTL (already expired)
    _invitation_row, token = repository.create_invitation(
        family_id=family_id,
        creator_user_id=owner.user_id,
        intended_role=FamilyRole.EDITOR,
        ttl_hours=-1,
    )

    recipient = create_test_user(repository, subject="recipient_exp", verifier=verifier)
    resp = client.post(f"/v1/invitations/{token}/accept", headers=recipient.headers)
    assert resp.status_code == 410
    assert resp.json()["error"]["code"] == INVITATION_EXPIRED


def test_revoked_invitation_acceptance_fails(
    auth_fixture: tuple[TestClient, Database, IdentityRepository, FakePrincipalVerifier],
) -> None:
    client, _, repository, verifier = auth_fixture
    owner = create_test_user(repository, subject="owner_rev", verifier=verifier)
    family_id = create_test_family(repository, name="Family Rev", owner=owner)

    create_resp = client.post(
        f"/v1/families/{family_id}/invitations",
        json={"role": "editor"},
        headers=owner.headers,
    )
    invitation_id = create_resp.json()["invitation_id"]
    token = create_resp.json()["invitation_url"].split("/invite/")[1]

    # Owner revokes
    revoke_resp = client.post(
        f"/v1/families/{family_id}/invitations/{invitation_id}/revoke",
        headers=owner.headers,
    )
    assert revoke_resp.status_code == 200
    assert revoke_resp.json()["status"] == "revoked"

    # Recipient tries to accept
    recipient = create_test_user(repository, subject="recipient_rev", verifier=verifier)
    accept_resp = client.post(f"/v1/invitations/{token}/accept", headers=recipient.headers)
    assert accept_resp.status_code == 410
    assert accept_resp.json()["error"]["code"] == INVITATION_REVOKED


def test_double_accept_different_users(
    auth_fixture: tuple[TestClient, Database, IdentityRepository, FakePrincipalVerifier],
) -> None:
    client, _, repository, verifier = auth_fixture
    owner = create_test_user(repository, subject="owner_dbl", verifier=verifier)
    family_id = create_test_family(repository, name="Family Dbl", owner=owner)

    create_resp = client.post(
        f"/v1/families/{family_id}/invitations",
        json={"role": "editor"},
        headers=owner.headers,
    )
    token = create_resp.json()["invitation_url"].split("/invite/")[1]

    user_a = create_test_user(repository, subject="user_a", verifier=verifier)
    user_b = create_test_user(repository, subject="user_b", verifier=verifier)

    resp_a = client.post(f"/v1/invitations/{token}/accept", headers=user_a.headers)
    assert resp_a.status_code == 200

    resp_b = client.post(f"/v1/invitations/{token}/accept", headers=user_b.headers)
    assert resp_b.status_code == 409
    assert resp_b.json()["error"]["code"] == INVITATION_ALREADY_ACCEPTED


def test_double_accept_same_user_is_idempotent(
    auth_fixture: tuple[TestClient, Database, IdentityRepository, FakePrincipalVerifier],
) -> None:
    client, _, repository, verifier = auth_fixture
    owner = create_test_user(repository, subject="owner_idem", verifier=verifier)
    family_id = create_test_family(repository, name="Family Idem", owner=owner)

    create_resp = client.post(
        f"/v1/families/{family_id}/invitations",
        json={"role": "editor"},
        headers=owner.headers,
    )
    token = create_resp.json()["invitation_url"].split("/invite/")[1]

    user_a = create_test_user(repository, subject="user_idem", verifier=verifier)

    resp1 = client.post(f"/v1/invitations/{token}/accept", headers=user_a.headers)
    assert resp1.status_code == 200
    assert resp1.json()["already_member"] is False

    resp2 = client.post(f"/v1/invitations/{token}/accept", headers=user_a.headers)
    assert resp2.status_code == 200
    assert resp2.json()["already_member"] is True


def test_accept_by_existing_member_does_not_modify_role(
    auth_fixture: tuple[TestClient, Database, IdentityRepository, FakePrincipalVerifier],
) -> None:
    client, database, repository, verifier = auth_fixture
    owner = create_test_user(repository, subject="owner_exist", verifier=verifier)
    family_id = create_test_family(repository, name="Family Exist", owner=owner)

    # An existing viewer
    viewer = create_test_user(repository, subject="viewer_exist", verifier=verifier)
    create_membership(database, family_id=family_id, user=viewer, role=FamilyRole.VIEWER)

    # Owner sends an editor invite
    create_resp = client.post(
        f"/v1/families/{family_id}/invitations",
        json={"role": "editor"},
        headers=owner.headers,
    )
    token = create_resp.json()["invitation_url"].split("/invite/")[1]

    # Existing viewer accepts the editor invite
    resp = client.post(f"/v1/invitations/{token}/accept", headers=viewer.headers)
    assert resp.status_code == 200
    assert resp.json()["already_member"] is True
    # Invariant: existing membership role is NOT changed by an old invitation
    assert resp.json()["role"] == "viewer"

    with database.session_factory() as session:
        membership = session.get(FamilyMembershipRow, resp.json()["membership_id"])
        assert membership is not None
        assert membership.role == "viewer"


def test_cannot_revoke_already_accepted_invitation(
    auth_fixture: tuple[TestClient, Database, IdentityRepository, FakePrincipalVerifier],
) -> None:
    client, _, repository, verifier = auth_fixture
    owner = create_test_user(repository, subject="owner_rev_acc", verifier=verifier)
    family_id = create_test_family(repository, name="Family Rev Acc", owner=owner)

    create_resp = client.post(
        f"/v1/families/{family_id}/invitations",
        json={"role": "editor"},
        headers=owner.headers,
    )
    invitation_id = create_resp.json()["invitation_id"]
    token = create_resp.json()["invitation_url"].split("/invite/")[1]

    recipient = create_test_user(repository, subject="recipient_acc", verifier=verifier)
    client.post(f"/v1/invitations/{token}/accept", headers=recipient.headers)

    # Owner attempts to revoke accepted invitation
    revoke_resp = client.post(
        f"/v1/families/{family_id}/invitations/{invitation_id}/revoke",
        headers=owner.headers,
    )
    assert revoke_resp.status_code == 409
    assert revoke_resp.json()["error"]["code"] == INVITATION_ALREADY_ACCEPTED


def test_foreign_family_cannot_list_or_revoke_invitations(
    auth_fixture: tuple[TestClient, Database, IdentityRepository, FakePrincipalVerifier],
) -> None:
    client, _, repository, verifier = auth_fixture
    owner_a = create_test_user(repository, subject="owner_fa", verifier=verifier)
    family_a_id = create_test_family(repository, name="Family A", owner=owner_a)

    owner_b = create_test_user(repository, subject="owner_fb", verifier=verifier)
    create_test_family(repository, name="Family B", owner=owner_b)

    create_resp = client.post(
        f"/v1/families/{family_a_id}/invitations",
        json={"role": "editor"},
        headers=owner_a.headers,
    )
    invitation_id = create_resp.json()["invitation_id"]

    # Owner B tries to list Family A's invitations
    list_resp = client.get(
        f"/v1/families/{family_a_id}/invitations",
        headers=owner_b.headers,
    )
    assert list_resp.status_code == 404

    # Owner B tries to revoke Family A's invitation
    revoke_resp = client.post(
        f"/v1/families/{family_a_id}/invitations/{invitation_id}/revoke",
        headers=owner_b.headers,
    )
    assert revoke_resp.status_code == 404


def test_token_never_logged(
    auth_fixture: tuple[TestClient, Database, IdentityRepository, FakePrincipalVerifier],
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, _, repository, verifier = auth_fixture
    owner = create_test_user(repository, subject="owner_log", verifier=verifier)
    family_id = create_test_family(repository, name="Family Log", owner=owner)

    with caplog.at_level(logging.DEBUG):
        create_resp = client.post(
            f"/v1/families/{family_id}/invitations",
            json={"role": "editor"},
            headers=owner.headers,
        )
        token = create_resp.json()["invitation_url"].split("/invite/")[1]

        recipient = create_test_user(repository, subject="recipient_log", verifier=verifier)
        client.post(f"/v1/invitations/{token}/accept", headers=recipient.headers)

    # Assert plaintext token does not appear in any Mura server log record
    mura_records = [r for r in caplog.records if r.name.startswith("mura")]
    assert len(mura_records) > 0
    for record in mura_records:
        assert token not in record.message
        assert token not in str(record.args)
        for val in record.__dict__.values():
            if isinstance(val, str):
                assert token not in val
