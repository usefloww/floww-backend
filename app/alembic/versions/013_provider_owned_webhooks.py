"""migration

Revision ID: 2a3b4c5d6e7f
Revises: 1f9fe24442c7
Create Date: 2025-10-31 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "2a3b4c5d6e7f"
down_revision = "1f9fe24442c7"
branch_labels = None
depends_on = None


def upgrade():
    # Add provider_id column (nullable)
    op.add_column(
        "incoming_webhooks",
        sa.Column("provider_id", sa.UUID(), nullable=True),
    )

    # Make trigger_id nullable
    op.alter_column(
        "incoming_webhooks",
        "trigger_id",
        existing_type=sa.UUID(),
        nullable=True,
    )

    # Add foreign key constraint for provider_id
    op.create_foreign_key(
        "incoming_webhooks_provider_id_fkey",
        "incoming_webhooks",
        "providers",
        ["provider_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Add check constraint to ensure exactly one of trigger_id or provider_id is set
    op.create_check_constraint(
        "webhook_owner_check",
        "incoming_webhooks",
        "(trigger_id IS NOT NULL AND provider_id IS NULL) OR "
        "(trigger_id IS NULL AND provider_id IS NOT NULL)",
    )


def downgrade():
    # Remove check constraint
    op.drop_constraint("webhook_owner_check", "incoming_webhooks", type_="check")

    # Remove foreign key constraint
    op.drop_constraint(
        "incoming_webhooks_provider_id_fkey", "incoming_webhooks", type_="foreignkey"
    )

    # Make trigger_id non-nullable again
    op.alter_column(
        "incoming_webhooks",
        "trigger_id",
        existing_type=sa.UUID(),
        nullable=False,
    )

    # Remove provider_id column
    op.drop_column("incoming_webhooks", "provider_id")
