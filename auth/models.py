"""Auth domain models.

Plain dataclasses — no ORM. The DB layer in this project uses sqlite3 directly
with a dict-row factory; we hydrate dataclasses from those rows on demand.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class User:
    id: int
    email: str
    role: str  # 'admin' | 'viewer'
    is_active: bool
    created_at: str
    password_changed_at: str
    last_login_at: Optional[str] = None
    # Future-proof slots — never populated by current code paths.
    totp_secret: Optional[str] = None
    oauth_provider: Optional[str] = None

    def is_admin(self) -> bool:
        return self.role == "admin"


@dataclass(frozen=True)
class RefreshTokenRecord:
    id: int
    token_hash: str
    user_id: int
    family_id: str
    parent_hash: Optional[str]
    expires_at: str
    revoked_at: Optional[str]
    created_at: str
    user_agent: Optional[str]
    ip: Optional[str]

    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def is_expired(self, now: datetime) -> bool:
        return now >= datetime.fromisoformat(self.expires_at)


@dataclass(frozen=True)
class AuthEvent:
    user_id: Optional[int]
    event_type: str  # login_success | login_failed | logout | refresh | password_change | role_change
    ip: Optional[str]
    user_agent: Optional[str]
    ts: str
    success: bool
    metadata_json: Optional[str]
