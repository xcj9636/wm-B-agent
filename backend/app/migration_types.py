"""Narrow Alembic type-comparison exceptions for development databases."""
from typing import Any, Optional

import sqlalchemy as sa


def compare_migration_types(
    context: Any,
    inspected_column: Any,
    metadata_column: Any,
    inspected_type: sa.types.TypeEngine,
    metadata_type: sa.types.TypeEngine,
) -> Optional[bool]:
    """Ignore only SQLite's lossy reflection of UUID-compatible columns."""
    del inspected_column, metadata_column
    if (
        context.dialect.name == "sqlite"
        and isinstance(metadata_type, sa.Uuid)
        and isinstance(inspected_type, (sa.CHAR, sa.Numeric))
    ):
        return False
    return None
