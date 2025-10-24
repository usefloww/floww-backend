"""add trigger metadata and webhook routing

Revision ID: a8b9c1d2e3f4
Revises: 587d99894f72
Create Date: 2025-10-24 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "a8b9c1d2e3f4"
down_revision = "587d99894f72"
branch_labels = None
depends_on = None


def upgrade():
    # Add triggers_metadata to workflows table
    op.add_column(
        "workflows",
        sa.Column("triggers_metadata", JSONB, nullable=True),
    )

    # Add path and method columns to incoming_webhooks table
    op.add_column(
        "incoming_webhooks",
        sa.Column("path", sa.Text(), nullable=True),  # nullable initially for migration
    )
    op.add_column(
        "incoming_webhooks",
        sa.Column("method", sa.Text(), nullable=True, server_default="POST"),
    )

    # Add unique constraint on (path, method)
    op.create_unique_constraint(
        "uq_incoming_webhook_path_method",
        "incoming_webhooks",
        ["path", "method"],
    )

    # Make path NOT NULL after setting defaults (if there are existing rows)
    # Note: If there are existing incoming_webhooks, you'll need to populate them first
    op.alter_column("incoming_webhooks", "path", nullable=False)
    op.alter_column("incoming_webhooks", "method", nullable=False)


def downgrade():
    # Drop unique constraint
    op.drop_constraint(
        "uq_incoming_webhook_path_method", "incoming_webhooks", type_="unique"
    )

    # Drop columns from incoming_webhooks
    op.drop_column("incoming_webhooks", "method")
    op.drop_column("incoming_webhooks", "path")

    # Drop triggers_metadata from workflows
    op.drop_column("workflows", "triggers_metadata")
