"""Identity and family foundation API.

Signing in and listing your own families need only a verified Principal, since
there is no family scope yet to authorize against. Everything addressed by
``family_id`` goes through the shared capability chain instead, so these routes
resolve membership exactly the same way the recording, profile and conflict
routes do -- one implementation, one place to get it wrong.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import Field

from apps.api.errors import (
    ACCOUNT_DELETION_REQUIRES_OWNER_TRANSFER,
    INVALID_INVITATION_ROLE,
    INVITATION_ALREADY_ACCEPTED,
    INVITATION_EXPIRED,
    INVITATION_NOT_FOUND,
    INVITATION_REVOKED,
    SOLE_OWNER_REQUIRED,
)
from mura.domain.models import StrictModel
from mura.identity.auth import Principal
from mura.identity.context import AuthorizedFamilyContext
from mura.identity.policy import Capability, FamilyRole, capabilities_for
from mura.storage.identity import (
    AccountDeletionBlockedError,
    FamilyDeleteAuthorizationError,
    IdentityRepository,
    InvalidInvitationRoleError,
    InvitationAlreadyAcceptedError,
    InvitationExpiredError,
    InvitationNotFoundError,
    InvitationRevokedError,
    MembershipNotFoundError,
    SoleOwnerError,
)


class UserView(StrictModel):
    """Safe self-description. Provider subject and issuer are not exposed."""

    user_id: str
    email: str | None = None
    display_name: str | None = None


class FamilyView(StrictModel):
    family_id: str
    name: str
    #: The caller's own role, so the UI can hide actions it may not perform.
    role: FamilyRole
    capabilities: list[Capability] = Field(default_factory=list)


class MemberView(StrictModel):
    """Minimal member data: no email, no issuer, no provider subject."""

    user_id: str
    display_name: str | None = None
    role: FamilyRole


class CreateFamilyRequest(StrictModel):
    name: str = Field(min_length=1, max_length=256)


class AccountDeletionResult(StrictModel):
    mura_data_deleted: bool
    identity_provider_account_deleted: bool = False
    requires_provider_sign_out: bool = True


def _not_found() -> HTTPException:
    # A private family and a nonexistent one are the same answer.
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")


def register_identity_routes(
    app: FastAPI,
    *,
    principal_dependency: Callable[..., Principal],
    family_read_dependency: Callable[..., object],
    read_members_dependency: Callable[..., object],
    identity_repository_dependency: Callable[..., object],
) -> None:
    def _repository(runtime: object) -> IdentityRepository:
        return cast(IdentityRepository, runtime)

    @app.get("/v1/me", response_model=UserView)
    def get_me(
        principal: Principal = Depends(principal_dependency),
    ) -> UserView:
        return UserView(
            user_id=principal.user_id,
            email=principal.email,
            display_name=principal.display_name,
        )

    @app.delete("/v1/me", response_model=AccountDeletionResult)
    def delete_me(
        principal: Principal = Depends(principal_dependency),
        repository: object = Depends(identity_repository_dependency),
    ) -> AccountDeletionResult:
        identity = _repository(repository)
        try:
            identity.delete_account(user_id=principal.user_id)
        except AccountDeletionBlockedError as exc:
            # The stable code is enough for the client to explain what action
            # is required; family ids stay private server-side metadata.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ACCOUNT_DELETION_REQUIRES_OWNER_TRANSFER,
            ) from exc
        return AccountDeletionResult(
            mura_data_deleted=True,
            identity_provider_account_deleted=False,
            requires_provider_sign_out=True,
        )

    @app.post("/v1/families", response_model=FamilyView, status_code=201)
    def create_family(
        request: CreateFamilyRequest,
        principal: Principal = Depends(principal_dependency),
        repository: object = Depends(identity_repository_dependency),
    ) -> FamilyView:
        # The owner is the authenticated principal; the body cannot name one.
        family = _repository(repository).create_family(
            name=request.name, owner_user_id=principal.user_id
        )
        return FamilyView(
            family_id=family.family_id,
            name=family.name,
            role=FamilyRole.OWNER,
            capabilities=sorted(capabilities_for(FamilyRole.OWNER)),
        )

    @app.get("/v1/families", response_model=list[FamilyView])
    def list_families(
        principal: Principal = Depends(principal_dependency),
        repository: object = Depends(identity_repository_dependency),
    ) -> list[FamilyView]:
        # Membership-scoped: legacy families with no members are never listed.
        return [
            FamilyView(
                family_id=family.family_id,
                name=family.name,
                role=role,
                capabilities=sorted(capabilities_for(role)),
            )
            for family, role in _repository(repository).list_families_for_user(principal.user_id)
        ]

    @app.get("/v1/families/{family_id}", response_model=FamilyView)
    def get_family(
        family_id: str,
        context: object = Depends(family_read_dependency),
    ) -> FamilyView:
        # The shared context already resolved family and role in one query, so
        # the route reads them rather than looking membership up a second time.
        authorized = cast(AuthorizedFamilyContext, context)
        return FamilyView(
            family_id=authorized.family.family_id,
            name=authorized.family.name,
            role=authorized.role,
            capabilities=sorted(authorized.capabilities),
        )

    @app.get("/v1/families/{family_id}/members", response_model=list[MemberView])
    def list_members(
        family_id: str,
        context: object = Depends(read_members_dependency),
        repository: object = Depends(identity_repository_dependency),
    ) -> list[MemberView]:
        del context  # Membership and capability already proven.
        identity = _repository(repository)
        return [
            MemberView(
                user_id=user.user_id,
                display_name=user.display_name,
                role=FamilyRole(row.role),
            )
            for row, user in identity.list_members(family_id)
        ]


class UpdateMemberRoleRequest(StrictModel):
    role: FamilyRole


class DeleteFamilyRequest(StrictModel):
    confirm_family_id: str


def register_membership_admin_routes(
    app: FastAPI,
    *,
    manage_members_dependency: Callable[..., object],
    delete_family_dependency: Callable[..., object],
    identity_repository_dependency: Callable[..., object],
    get_runtime_dependency: Callable[..., object] | None = None,
) -> None:
    """Owner-only membership administration.

    New in PR-03, so these are Principal-native from the start. Adding a member
    is deliberately absent: it needs an invitation lifecycle, and faking one by
    email would create memberships nobody agreed to.
    """

    @app.patch("/v1/families/{family_id}/members/{user_id}", response_model=MemberView)
    def update_member_role(
        family_id: str,
        user_id: str,
        request: UpdateMemberRoleRequest,
        context: object = Depends(manage_members_dependency),
        repository: object = Depends(identity_repository_dependency),
    ) -> MemberView:
        del context  # Authorization already proven by the dependency.
        identity = cast(IdentityRepository, repository)
        try:
            # The service checks the owner count under row locks; the route must
            # never re-implement a weaker version of that invariant.
            membership = identity.change_member_role(
                family_id=family_id, user_id=user_id, role=request.role
            )
        except MembershipNotFoundError as exc:
            raise _not_found() from exc
        except SoleOwnerError as exc:
            raise sole_owner_error() from exc
        user = identity.get_user(user_id)
        return MemberView(
            user_id=user_id,
            display_name=user.display_name if user else None,
            role=FamilyRole(membership.role),
        )

    @app.delete("/v1/families/{family_id}/members/{user_id}", status_code=204)
    def remove_member(
        family_id: str,
        user_id: str,
        context: object = Depends(manage_members_dependency),
        repository: object = Depends(identity_repository_dependency),
    ) -> None:
        del context
        identity = cast(IdentityRepository, repository)
        try:
            identity.remove_member(family_id=family_id, user_id=user_id)
        except MembershipNotFoundError as exc:
            # A membership outside this family is simply not found.
            raise _not_found() from exc
        except SoleOwnerError as exc:
            raise sole_owner_error() from exc

    @app.delete("/v1/families/{family_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_family(
        family_id: str,
        request: DeleteFamilyRequest,
        context: object = Depends(delete_family_dependency),
        repository: object = Depends(identity_repository_dependency),
        runtime: object = Depends(get_runtime_dependency) if get_runtime_dependency else None,
    ) -> None:
        if request.confirm_family_id != family_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="confirmation family_id does not match",
            )

        authorized = cast(AuthorizedFamilyContext, context)
        if authorized.role != FamilyRole.OWNER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="insufficient_family_role",
            )

        identity = cast(IdentityRepository, repository)
        settings = getattr(runtime, "settings", None)
        audio_backend = getattr(
            getattr(settings, "audio_storage_backend", "local"),
            "value",
            getattr(settings, "audio_storage_backend", "local"),
        )
        book_backend = getattr(
            getattr(settings, "book_storage_backend", "local"),
            "value",
            getattr(settings, "book_storage_backend", "local"),
        )
        max_attempts = getattr(settings, "storage_cleanup_max_attempts", 8)
        try:
            result = identity.delete_family(
                family_id,
                requesting_user_id=authorized.user_id,
                default_audio_backend=str(audio_backend),
                default_book_backend=str(book_backend),
                cleanup_max_attempts=max_attempts,
            )
        except MembershipNotFoundError as exc:
            raise _not_found() from exc
        except FamilyDeleteAuthorizationError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="insufficient_family_role",
            ) from exc
        except SoleOwnerError as exc:
            raise sole_owner_error() from exc
        if result is None:
            raise _not_found()


def sole_owner_error() -> HTTPException:
    """A distinguishable code: the client must explain *why* the change failed.

    "A family must always keep an owner" is actionable guidance, unlike a
    generic conflict, and it reveals nothing the caller -- already an owner of
    this family -- does not know.
    """

    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=SOLE_OWNER_REQUIRED)


def _aware_req(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _aware_opt(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


class CreateInvitationRequest(StrictModel):
    role: FamilyRole = FamilyRole.EDITOR


class InvitationView(StrictModel):
    invitation_id: str
    family_id: str
    intended_role: FamilyRole
    status: str
    expires_at: datetime
    created_at: datetime
    accepted_at: datetime | None = None
    revoked_at: datetime | None = None


class InvitationCreatedView(InvitationView):
    invitation_url: str


class InvitationPreviewView(StrictModel):
    family_name: str
    inviter_name: str | None = None
    intended_role: FamilyRole
    status: str
    expires_at: datetime


class InvitationAcceptResultView(StrictModel):
    family_id: str
    role: FamilyRole
    already_member: bool
    membership_id: str


def register_invitation_routes(
    app: FastAPI,
    *,
    principal_dependency: Callable[..., Principal],
    manage_members_dependency: Callable[..., object],
    identity_repository_dependency: Callable[..., object],
) -> None:
    def _repository(runtime: object) -> IdentityRepository:
        return cast(IdentityRepository, runtime)

    @app.post(
        "/v1/families/{family_id}/invitations",
        response_model=InvitationCreatedView,
        status_code=status.HTTP_201_CREATED,
    )
    def create_invitation(
        family_id: str,
        request: CreateInvitationRequest,
        context: object = Depends(manage_members_dependency),
        repository: object = Depends(identity_repository_dependency),
    ) -> InvitationCreatedView:
        authorized = cast(AuthorizedFamilyContext, context)
        identity = _repository(repository)
        try:
            invitation, token = identity.create_invitation(
                family_id=family_id,
                creator_user_id=authorized.user_id,
                intended_role=request.role,
            )
        except InvalidInvitationRoleError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=INVALID_INVITATION_ROLE,
            ) from exc
        return InvitationCreatedView(
            invitation_id=invitation.invitation_id,
            family_id=invitation.family_id,
            intended_role=FamilyRole(invitation.intended_role),
            status=invitation.status,
            expires_at=_aware_req(invitation.expires_at),
            created_at=_aware_req(invitation.created_at),
            accepted_at=_aware_opt(invitation.accepted_at),
            revoked_at=_aware_opt(invitation.revoked_at),
            invitation_url=f"/invite/{token}",
        )

    @app.get(
        "/v1/families/{family_id}/invitations",
        response_model=list[InvitationView],
    )
    def list_invitations(
        family_id: str,
        context: object = Depends(manage_members_dependency),
        repository: object = Depends(identity_repository_dependency),
    ) -> list[InvitationView]:
        del context
        identity = _repository(repository)
        rows = identity.list_invitations(family_id)
        return [
            InvitationView(
                invitation_id=row.invitation_id,
                family_id=row.family_id,
                intended_role=FamilyRole(row.intended_role),
                status=row.status,
                expires_at=_aware_req(row.expires_at),
                created_at=_aware_req(row.created_at),
                accepted_at=_aware_opt(row.accepted_at),
                revoked_at=_aware_opt(row.revoked_at),
            )
            for row in rows
        ]

    @app.post(
        "/v1/families/{family_id}/invitations/{invitation_id}/revoke",
        response_model=InvitationView,
    )
    def revoke_invitation(
        family_id: str,
        invitation_id: str,
        context: object = Depends(manage_members_dependency),
        repository: object = Depends(identity_repository_dependency),
    ) -> InvitationView:
        authorized = cast(AuthorizedFamilyContext, context)
        identity = _repository(repository)
        try:
            row = identity.revoke_invitation(
                family_id=family_id,
                invitation_id=invitation_id,
                revoker_user_id=authorized.user_id,
            )
        except InvitationNotFoundError as exc:
            raise _not_found() from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=INVITATION_ALREADY_ACCEPTED,
            ) from exc
        return InvitationView(
            invitation_id=row.invitation_id,
            family_id=row.family_id,
            intended_role=FamilyRole(row.intended_role),
            status=row.status,
            expires_at=_aware_req(row.expires_at),
            created_at=_aware_req(row.created_at),
            accepted_at=_aware_opt(row.accepted_at),
            revoked_at=_aware_opt(row.revoked_at),
        )

    @app.get(
        "/v1/invitations/{token}/preview",
        response_model=InvitationPreviewView,
    )
    def preview_invitation(
        token: str,
        repository: object = Depends(identity_repository_dependency),
    ) -> InvitationPreviewView:
        identity = _repository(repository)
        try:
            invitation, family, inviter = identity.get_invitation_preview(token)
        except InvitationNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=INVITATION_NOT_FOUND,
            ) from exc
        return InvitationPreviewView(
            family_name=family.name,
            inviter_name=inviter.display_name if inviter else None,
            intended_role=FamilyRole(invitation.intended_role),
            status=invitation.status,
            expires_at=_aware_req(invitation.expires_at),
        )

    @app.post(
        "/v1/invitations/{token}/accept",
        response_model=InvitationAcceptResultView,
    )
    def accept_invitation(
        token: str,
        principal: Principal = Depends(principal_dependency),
        repository: object = Depends(identity_repository_dependency),
    ) -> InvitationAcceptResultView:
        identity = _repository(repository)
        try:
            invitation, membership, already_member = identity.accept_invitation(
                token=token,
                user_id=principal.user_id,
            )
        except InvitationNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=INVITATION_NOT_FOUND,
            ) from exc
        except InvitationRevokedError as exc:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail=INVITATION_REVOKED,
            ) from exc
        except InvitationExpiredError as exc:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail=INVITATION_EXPIRED,
            ) from exc
        except InvitationAlreadyAcceptedError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=INVITATION_ALREADY_ACCEPTED,
            ) from exc
        return InvitationAcceptResultView(
            family_id=invitation.family_id,
            role=FamilyRole(membership.role),
            already_member=already_member,
            membership_id=membership.membership_id,
        )


__all__ = [
    "AccountDeletionResult",
    "CreateFamilyRequest",
    "CreateInvitationRequest",
    "DeleteFamilyRequest",
    "FamilyView",
    "InvitationAcceptResultView",
    "InvitationCreatedView",
    "InvitationPreviewView",
    "InvitationView",
    "MemberView",
    "SoleOwnerError",
    "UserView",
    "register_identity_routes",
    "register_invitation_routes",
    "register_membership_admin_routes",
    "sole_owner_error",
]
