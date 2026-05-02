"""create tasks

Revision ID: 0001_create_tasks
Revises: 
Create Date: 2026-05-02 08:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0001_create_tasks"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("content", sa.String(length=255), nullable=False),
        sa.Column("remarks", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_recurring", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("trigger_time", sa.DateTime(), nullable=True),
        sa.Column("cron_expr", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("snooze_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chat_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )


def downgrade() -> None:
    op.drop_table("tasks")
