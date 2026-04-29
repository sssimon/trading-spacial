"""Authentication subsystem for trading-spacial.

Layout:
- models.py        — User, RefreshToken, AuthEvent dataclasses
- password.py      — passlib/bcrypt wrappers (hash, verify, dummy_verify)
- tokens.py        — JWT issuance + refresh rotation + theft detection
- audit.py         — log_auth_event (failure-tolerant, separate transaction)
- rate_limit.py    — in-memory IP+email tracking for /auth/login
- dependencies.py  — get_current_user, require_role, require_csrf
- middleware.py    — ASGI middleware enforcing JWT on non-whitelisted paths
"""
