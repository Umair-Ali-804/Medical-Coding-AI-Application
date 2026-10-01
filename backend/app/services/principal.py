from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import Role


@dataclass(frozen=True)
class Principal:
    """Authenticated actor: a user, an API key (integration), or the system itself."""

    id: str
    type: str  # user | api_key | system
    role: Role
    label: str
    ip: str | None = None

    @property
    def is_user(self) -> bool:
        return self.type == "user"


SYSTEM = Principal(id="system", type="system", role=Role.ADMIN, label="system")
