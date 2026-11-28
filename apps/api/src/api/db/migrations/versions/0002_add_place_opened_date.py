"""add place opened_date

신규 개원 지점의 개원일(YYYY.MM.DD). 신규 개원이 아니면 비어 있다(이슈 #101).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-29 04:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # SQLite는 열 삭제를 바로 지원하지 않아 되돌리기와 같이 batch로 바꾼다.
    with op.batch_alter_table("place") as batch:
        batch.add_column(sa.Column("opened_date", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("place") as batch:
        batch.drop_column("opened_date")
