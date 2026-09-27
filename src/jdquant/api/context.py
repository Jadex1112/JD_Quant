"""Application wiring and settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jdquant.persistence.store import Store
from jdquant.platform import Platform, build_paper_platform
from jdquant.security.audit import AuditLog
from jdquant.security.identity import IdentityService
from jdquant.security.secrets import SecretBox, SecretStore, load_master_key


def _flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    return default if value is None else value.lower() in ("1", "true", "yes")


@dataclass
class Settings:
    data_dir: Path | None = None
    cookie_secure: bool = False
    enforce_mfa_for_privileged: bool = True
    allow_setup: bool = True

    @classmethod
    def from_env(cls) -> Settings:
        data_dir = os.environ.get("JDQ_DATA_DIR", "data")
        return cls(
            data_dir=Path(data_dir) if data_dir != ":memory:" else None,
            cookie_secure=_flag("JDQ_COOKIE_SECURE", False),
            enforce_mfa_for_privileged=_flag("JDQ_ENFORCE_MFA", True),
            allow_setup=_flag("JDQ_ALLOW_SETUP", True),
        )


@dataclass
class AppContext:
    platform: Platform
    store: Store
    audit: AuditLog
    identity: IdentityService
    secrets: SecretStore
    settings: Settings
    services: dict[str, Any] = field(default_factory=dict)


def build_context(settings: Settings | None = None, platform: Platform | None = None) -> AppContext:
    settings = settings or Settings.from_env()
    if platform is None:
        store = Store(settings.data_dir / "jdquant.db") if settings.data_dir else Store(":memory:")
        platform = build_paper_platform(store=store)
    store = platform.store or Store(":memory:")
    box = SecretBox(load_master_key(settings.data_dir))
    audit = AuditLog(store, platform.clock)
    identity = IdentityService(
        store, platform.clock, audit, box, enforce_mfa_for_privileged=settings.enforce_mfa_for_privileged
    )
    return AppContext(platform, store, audit, identity, SecretStore(store, box), settings)
