"""Identity and family foundation API.

These are the first routes to use the new Principal dependency. The existing
recording, job, profile and conflict routes deliberately keep their current
authentication until PR-03B flips them all at once: partial enforcement would be
worse than none, because it would look protected.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import Field

from mura.domain.models import StrictModel
from mura.identity.auth import Principal
from mura.identity.policy import Capability, FamilyRole, capabilities_for, role_allows
from mura.storage.identity import IdentityRepository, SoleOwnerError


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


def _not_found() -> HTTPException:
    # A private family and a nonexistent one are the same answer.
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")


def _forbidden() -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient role")


def register_identity_routes(
    app: FastAPI,
    *,
    principal_dependency: Callable[..., Principal],
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
        principal: Principal = Depends(principal_dependency),
        repository: object = Depends(identity_repository_dependency),
    ) -> FamilyView:
        identity = _repository(repository)
        family = identity.get_family_for_member(family_id=family_id, user_id=principal.user_id)
        if family is None:
            raise _not_found()
        membership = identity.get_membership(family_id=family_id, user_id=principal.user_id)
        role = FamilyRole(membership.role) if membership else FamilyRole.VIEWER
        return FamilyView(
            family_id=family.family_id,
            name=family.name,
            role=role,
            capabilities=sorted(capabilities_for(role)),
        )

    @app.get("/v1/families/{family_id}/members", response_model=list[MemberView])
    def list_members(
        family_id: str,
        principal: Principal = Depends(principal_dependency),
        repository: object = Depends(identity_repository_dependency),
    ) -> list[MemberView]:
        identity = _repository(repository)
        membership = identity.get_membership(family_id=family_id, user_id=principal.user_id)
        if membership is None:
            # Non-member: do not confirm the family exists.
            raise _not_found()
        if not role_allows(FamilyRole(membership.role), Capability.READ_MEMBERS):
            raise _forbidden()
        return [
            MemberView(
                user_id=user.user_id,
                display_name=user.display_name,
                role=FamilyRole(row.role),
            )
            for row, user in identity.list_members(family_id)
        ]


def sole_owner_error() -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="sole owner required")


__all__ = [
    "CreateFamilyRequest",
    "FamilyView",
    "MemberView",
    "SoleOwnerError",
    "UserView",
    "register_identity_routes",
    "sole_owner_error",
]
