"""migration

Revision ID: 3b4c5d6e7f8g
Revises: ae804c8e4248
Create Date: 2025-11-07 20:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "3b4c5d6e7f8g"
down_revision = "ae804c8e4248"
branch_labels = None
depends_on = None


def upgrade():
    # Create kv_tables table
    op.create_table(
        "kv_tables",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("namespace_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["namespace_id"], ["namespaces.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("namespace_id", "name", name="uq_namespace_table_name"),
    )
    op.create_index("idx_kv_tables_namespace", "kv_tables", ["namespace_id"])

    # Create kv_table_permissions table
    op.create_table(
        "kv_table_permissions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("table_id", sa.UUID(), nullable=False),
        sa.Column("workflow_id", sa.UUID(), nullable=False),
        sa.Column("can_read", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("can_write", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["table_id"], ["kv_tables.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "table_id", "workflow_id", name="uq_table_workflow_permission"
        ),
    )
    op.create_index(
        "idx_kv_permissions_table", "kv_table_permissions", ["table_id"]
    )
    op.create_index(
        "idx_kv_permissions_workflow", "kv_table_permissions", ["workflow_id"]
    )

    # Create kv_items table
    op.create_table(
        "kv_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("table_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["table_id"], ["kv_tables.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("table_id", "key", name="uq_table_key"),
    )
    op.create_index("idx_kv_items_table", "kv_items", ["table_id"])
    op.create_index("idx_kv_items_table_key", "kv_items", ["table_id", "key"])


def downgrade():
    # Drop tables in reverse order
    op.drop_index("idx_kv_items_table_key", table_name="kv_items")
    op.drop_index("idx_kv_items_table", table_name="kv_items")
    op.drop_table("kv_items")

    op.drop_index("idx_kv_permissions_workflow", table_name="kv_table_permissions")
    op.drop_index("idx_kv_permissions_table", table_name="kv_table_permissions")
    op.drop_table("kv_table_permissions")

    op.drop_index("idx_kv_tables_namespace", table_name="kv_tables")
    op.drop_table("kv_tables")
