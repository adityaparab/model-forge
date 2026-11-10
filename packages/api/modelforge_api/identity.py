"""The acting identity of a write: the ``X-ModelForge-User`` header and the role map.

A pluggable identity (umbrella 10.5): the header names the actor, ``MODELFORGE_USERS``
grants roles, and an unset map grants every role to every identity (the demo, the tests).
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header

from modelforge_api.deps import get_settings
from modelforge_api.errors import ApiError
from modelforge_api.settings import ROLES, ApiSettings

__all__ = ["IDENTITY_HEADER", "NAME", "Identity", "require"]

IDENTITY_HEADER = "X-ModelForge-User"
NAME = re.compile(r"[A-Za-z0-9._@-]{1,64}")


@dataclass(frozen=True)
class Identity:
    name: str
    roles: frozenset[str]


def require(role: str | None = None) -> Callable[..., Identity]:
    """A FastAPI dependency returning the request's `Identity`.

    ``401`` kind ``identity`` when the header is missing or malformed; ``403`` kind ``role``
    when a role map is configured and does not grant ``role`` to the identity.
    """
    if role is not None and role not in ROLES:
        raise ValueError(f"unknown role {role!r}; roles: {', '.join(sorted(ROLES))}")

    def identity(
        settings: Annotated[ApiSettings, Depends(get_settings)],
        user: Annotated[
            str | None,
            Header(
                alias=IDENTITY_HEADER,
                description="The acting identity: 1 to 64 characters of [A-Za-z0-9._@-].",
            ),
        ] = None,
    ) -> Identity:
        if user is None:
            raise ApiError(401, "identity", f"missing {IDENTITY_HEADER} header")
        if NAME.fullmatch(user) is None:
            raise ApiError(
                401,
                "identity",
                f"malformed {IDENTITY_HEADER} header: 1 to 64 characters of [A-Za-z0-9._@-]",
            )
        roles = ROLES if settings.users is None else settings.users.get(user, frozenset())
        if role is not None and role not in roles:
            raise ApiError(403, "role", f"{user} lacks the {role} role", role=role)
        return Identity(name=user, roles=roles)

    return identity
