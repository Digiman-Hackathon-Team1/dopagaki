"""add catalog tables

Revision ID: 0001
Revises:
Create Date: 2026-11-27 17:03:07.898423

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "area",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name_ja", sa.String(), nullable=False),
        sa.Column("name_ko", sa.String(), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lng", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "catalog_meta",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("checked_date", sa.String(), nullable=False),
        sa.Column("vat_rate", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "concern",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "fx_rate",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("krw_per_100_jpy", sa.Float(), nullable=False),
        sa.Column("date_label", sa.String(), nullable=False),
        sa.Column("bank_ko", sa.String(), nullable=False),
        sa.Column("bank_ja", sa.String(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "menu",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("name_ja", sa.String(), nullable=False),
        sa.Column("name_ko", sa.String(), nullable=False),
        sa.Column("short_ja", sa.String(), nullable=False),
        sa.Column("short_ko", sa.String(), nullable=False),
        sa.Column("jp_benchmark_jpy", sa.Integer(), nullable=False),
        sa.Column("vat_applies_in_korea", sa.Boolean(), nullable=False),
        sa.Column("lead_time_note_ja", sa.Text(), nullable=False),
        sa.Column("lead_time_note_ko", sa.Text(), nullable=False),
        sa.Column("downtime_max_days", sa.Integer(), nullable=False),
        sa.Column("uses_needle", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "snapshot_source",
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("path"),
    )
    op.create_table(
        "concern_menu",
        sa.Column("concern_id", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("menu_id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["concern_id"],
            ["concern.id"],
        ),
        sa.ForeignKeyConstraint(
            ["menu_id"],
            ["menu.id"],
        ),
        sa.PrimaryKeyConstraint("concern_id", "position"),
    )
    op.create_table(
        "jp_benchmark",
        sa.Column("menu_id", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name_ja", sa.String(), nullable=False),
        sa.Column("name_ko", sa.String(), nullable=False),
        sa.Column("plan_ja", sa.Text(), nullable=False),
        sa.Column("plan_ko", sa.Text(), nullable=False),
        sa.Column("price_jpy", sa.Integer(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["menu_id"],
            ["menu.id"],
        ),
        sa.PrimaryKeyConstraint("menu_id", "position"),
    )
    op.create_table(
        "place",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("area_id", sa.String(), nullable=False),
        sa.Column("name_ja", sa.String(), nullable=False),
        sa.Column("name_ko", sa.String(), nullable=False),
        sa.Column("brand_ja", sa.String(), nullable=False),
        sa.Column("address_ko", sa.Text(), nullable=False),
        sa.Column("station_ja", sa.String(), nullable=False),
        sa.Column("station_ko", sa.String(), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lng", sa.Float(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("vat_excluded", sa.Boolean(), nullable=False),
        sa.Column("japanese_support", sa.Boolean(), nullable=False),
        sa.Column("caveat_ja", sa.Text(), nullable=False),
        sa.Column("caveat_ko", sa.Text(), nullable=False),
        sa.Column("ai_rating", sa.Float(), nullable=False),
        sa.Column("rating_scope", sa.String(), nullable=False),
        sa.Column("rating_tier", sa.String(), nullable=False),
        sa.Column("central_channel", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["area_id"],
            ["area.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "channel",
        sa.Column("place_id", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("label_ja", sa.String(), nullable=False),
        sa.Column("label_ko", sa.String(), nullable=False),
        sa.Column("url_ja", sa.Text(), nullable=False),
        sa.Column("url_ko", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["place_id"],
            ["place.id"],
        ),
        sa.PrimaryKeyConstraint("place_id", "position"),
    )
    op.create_table(
        "offer",
        sa.Column("place_id", sa.String(), nullable=False),
        sa.Column("menu_id", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("krw", sa.Integer(), nullable=False),
        sa.Column("note_ja", sa.Text(), nullable=False),
        sa.Column("note_ko", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["menu_id"],
            ["menu.id"],
        ),
        sa.ForeignKeyConstraint(
            ["place_id"],
            ["place.id"],
        ),
        sa.PrimaryKeyConstraint("place_id", "menu_id"),
    )
    op.create_table(
        "proof",
        sa.Column("place_id", sa.String(), nullable=False),
        sa.Column("menu_id", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("url_ja", sa.Text(), nullable=False),
        sa.Column("url_ko", sa.Text(), nullable=False),
        sa.Column("domain_ja", sa.String(), nullable=False),
        sa.Column("domain_ko", sa.String(), nullable=False),
        sa.Column("captured_date", sa.String(), nullable=False),
        sa.Column("has_screenshot", sa.Boolean(), nullable=False),
        sa.Column("matched_text", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["menu_id"],
            ["menu.id"],
        ),
        sa.ForeignKeyConstraint(
            ["place_id"],
            ["place.id"],
        ),
        sa.PrimaryKeyConstraint("place_id", "menu_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("proof")
    op.drop_table("offer")
    op.drop_table("channel")
    op.drop_table("place")
    op.drop_table("jp_benchmark")
    op.drop_table("concern_menu")
    op.drop_table("snapshot_source")
    op.drop_table("menu")
    op.drop_table("fx_rate")
    op.drop_table("concern")
    op.drop_table("catalog_meta")
    op.drop_table("area")
