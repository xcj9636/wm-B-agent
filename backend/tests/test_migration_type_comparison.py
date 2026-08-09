from types import SimpleNamespace

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from app.migration_types import compare_migration_types


def context_for(dialect_name):
    return SimpleNamespace(dialect=SimpleNamespace(name=dialect_name))


def test_sqlite_uuid_reflection_noise_is_ignored():
    sqlite = context_for("sqlite")

    assert compare_migration_types(
        sqlite, None, None, sa.CHAR(32), UUID(as_uuid=True)
    ) is False
    assert compare_migration_types(
        sqlite, None, None, sa.Numeric(), UUID(as_uuid=True)
    ) is False


def test_other_type_comparisons_remain_enabled():
    assert compare_migration_types(
        context_for("sqlite"), None, None, sa.String(), sa.Integer()
    ) is None
    assert compare_migration_types(
        context_for("postgresql"),
        None,
        None,
        sa.CHAR(32),
        UUID(as_uuid=True),
    ) is None
