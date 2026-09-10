"""merge alembic heads: 20260702_01 + 20260709_01

Both revisions branch off 20260701_01, leaving the migration tree with two
heads. `alembic upgrade head` (run by entrypoint.sh on agent-registry startup)
then aborts with "Multiple head revisions are present", crashing the container.
This empty merge revision reconciles the two heads into one.
"""
from typing import Sequence, Union

revision: str = "20260713_01"
down_revision: Union[str, Sequence[str], None] = ("20260702_01", "20260709_01")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
