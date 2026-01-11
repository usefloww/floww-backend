"""Add manual trigger support

Revision ID: 033_manual_triggers
Revises: 255dd988c311
Create Date: 2026-01-11 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "033_manual_triggers"
down_revision = "255dd988c311"
branch_labels = None
depends_on = None


def upgrade():
    # Add triggered_by_user_id column to execution_history table
    op.add_column(
        "execution_history",
        sa.Column("triggered_by_user_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_execution_history_triggered_by_user",
        "execution_history",
        "users",
        ["triggered_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade():
    # Remove foreign key constraint and column
    op.drop_constraint(
        "fk_execution_history_triggered_by_user",
        "execution_history",
        type_="foreignkey",
    )
    op.drop_column("execution_history", "triggered_by_user_id")
