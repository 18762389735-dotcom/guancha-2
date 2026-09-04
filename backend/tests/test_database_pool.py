from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from psycopg.rows import dict_row

import guancha_api.auth.dependencies as auth_dependencies
import guancha_api.main as main_module
from guancha_api.auth.models import AppUser
from guancha_api.auth.fake import FakeTokenVerifier
from guancha_api.application.task_runners import ManualTaskRunner
from guancha_api.auth.dependencies import _request_repository, _resolve_authenticated_user
from guancha_api.main import create_app


class _Connection:
    def __init__(self) -> None:
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


class _Lease:
    def __init__(self, pool: "_Pool", connection: _Connection) -> None:
        self.pool = pool
        self.connection = connection

    async def __aenter__(self) -> _Connection:
        self.pool.entered += 1
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        self.pool.returned += 1
        return False


class _Pool:
    def __init__(self, **options: object) -> None:
        self.options = options
        self.connection_object = _Connection()
        self.entered = 0
        self.returned = 0
        self.open_calls = 0
        self.close_calls = 0

    def connection(self) -> _Lease:
        return _Lease(self, self.connection_object)

    async def open(self) -> None:
        self.open_calls += 1

    async def close(self) -> None:
        self.close_calls += 1


class _PooledRepository:
    instances: list["_PooledRepository"] = []

    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        now = datetime.now(timezone.utc)
        self.user = AppUser(
            id=uuid4(),
            cloudbase_user_id="cloudbase-user-a",
            created_at=now,
            updated_at=now,
        )
        self.__class__.instances.append(self)

    async def resolve_or_create_app_user(self, _subject: str) -> AppUser:
        return self.user


class _LegacyRepository:
    def __init__(self) -> None:
        self.recover_calls = 0
        self.close_calls = 0

    async def recover_interrupted_jobs(self, **_: object) -> None:
        self.recover_calls += 1

    async def close(self) -> None:
        self.close_calls += 1


def _request(pool: _Pool, **state_values: object) -> SimpleNamespace:
    state = SimpleNamespace(database_pool=pool, **state_values)
    return SimpleNamespace(app=SimpleNamespace(state=state))


@pytest.mark.asyncio
async def test_lifespan_opens_and_closes_owned_pool_with_conservative_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    pool_instances: list[_Pool] = []
    legacy_repositories: list[_LegacyRepository] = []

    def pool_factory(**options: object) -> _Pool:
        pool = _Pool(**options)
        pool_instances.append(pool)
        return pool

    async def connect(_cls, _dsn: str) -> _LegacyRepository:
        repository = _LegacyRepository()
        legacy_repositories.append(repository)
        return repository

    monkeypatch.setenv("GUANCHA_DATABASE_URL", "postgresql://local-only.invalid/test")
    monkeypatch.delenv("GUANCHA_DB_POOL_MIN_SIZE", raising=False)
    monkeypatch.delenv("GUANCHA_DB_POOL_MAX_SIZE", raising=False)
    monkeypatch.delenv("GUANCHA_DB_POOL_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setattr(main_module, "AsyncConnectionPool", pool_factory)
    monkeypatch.setattr(main_module.PostgresPhase2Repository, "connect", classmethod(connect))

    app = create_app(task_runner=ManualTaskRunner())
    async with app.router.lifespan_context(app):
        assert len(pool_instances) == 1
        assert pool_instances[0].options == {
            "conninfo": "postgresql://local-only.invalid/test",
            "kwargs": {"autocommit": True, "row_factory": dict_row},
            "min_size": 1,
            "max_size": 3,
            "timeout": 5.0,
            "check": main_module._DATABASE_POOL_CHECK,
            "open": False,
        }
        assert pool_instances[0].open_calls == 1
        assert app.state.database_pool is pool_instances[0]

    assert pool_instances[0].close_calls == 1
    assert legacy_repositories[0].recover_calls == 1
    assert legacy_repositories[0].close_calls == 1


@pytest.mark.asyncio
async def test_request_repository_uses_pool_lease_and_returns_connection_without_closing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _Pool()
    factory_calls = 0

    async def unexpected_factory():
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("worker repository factory must not be used when a pool is active")

    monkeypatch.setattr(auth_dependencies, "PostgresPhase2Repository", _PooledRepository)
    async with _request_repository(_request(pool, worker_repository_factory=unexpected_factory)) as repository:
        assert repository.connection is pool.connection_object
        assert pool.entered == 1

    assert pool.returned == 1
    assert pool.connection_object.close_calls == 0
    assert factory_calls == 0


@pytest.mark.asyncio
async def test_authenticated_user_resolution_uses_pool_without_new_connect_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _Pool()
    factory_calls = 0
    connect_calls = 0

    async def unexpected_factory():
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("worker repository factory must not be used when a pool is active")

    async def unexpected_connect(*_args, **_kwargs):
        nonlocal connect_calls
        connect_calls += 1
        raise AssertionError("ordinary authenticated requests must not connect directly")

    monkeypatch.setattr(auth_dependencies, "PostgresPhase2Repository", _PooledRepository)
    monkeypatch.setattr(auth_dependencies.PostgresPhase2Repository, "connect", unexpected_connect, raising=False)
    request = _request(
        pool,
        token_verifier=FakeTokenVerifier(),
        worker_repository_factory=unexpected_factory,
    )

    first = await _resolve_authenticated_user(request, "Bearer valid-token-a")
    second = await _resolve_authenticated_user(request, "Bearer valid-token-a")

    assert first.id != second.id
    assert pool.entered == pool.returned == 2
    assert pool.connection_object.close_calls == 0
    assert factory_calls == 0
    assert connect_calls == 0


def test_pool_settings_reject_invalid_or_oversized_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GUANCHA_DB_POOL_MIN_SIZE", "4")
    monkeypatch.setenv("GUANCHA_DB_POOL_MAX_SIZE", "3")
    with pytest.raises(RuntimeError, match="greater than or equal"):
        main_module._database_pool("postgresql://local-only.invalid/test")

    monkeypatch.setenv("GUANCHA_DB_POOL_MIN_SIZE", "not-a-number")
    with pytest.raises(RuntimeError, match="GUANCHA_DB_POOL_MIN_SIZE"):
        main_module._database_pool("postgresql://local-only.invalid/test")
