"""Explore Demo schemas (see `aqua_api.demo_mode`)."""

import uuid
from typing import Literal

from pydantic import BaseModel


class DemoSessionOut(BaseModel):
    """A read-only demo session. Use `access_token` as a Bearer token until `expires_in` seconds
    have passed; there is no refresh token (ask for a new session instead)."""

    access_token: str
    expires_in: int
    demo: Literal[True] = True
    org_id: uuid.UUID
    project_ids: list[uuid.UUID]
    banner: str


class DemoStatusOut(BaseModel):
    enabled: bool
    banner: str
