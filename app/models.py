import uuid
from datetime import datetime
from enum import Enum
from typing import Optional, Union
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class WorkflowDeploymentStatus(Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    FAILED = "failed"


class RuntimeCreationStatus(Enum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class OrganizationRole(Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    workos_user_id: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    owned_namespaces: Mapped[list["Namespace"]] = relationship(
        "Namespace", foreign_keys="Namespace.user_owner_id", back_populates="user_owner"
    )
    organization_memberships: Mapped[list["OrganizationMember"]] = relationship(
        back_populates="user"
    )
    created_workflows: Mapped[list["Workflow"]] = relationship(
        back_populates="created_by"
    )
    deployments: Mapped[list["WorkflowDeployment"]] = relationship(
        foreign_keys="WorkflowDeployment.deployed_by_id", back_populates="deployed_by"
    )


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    members: Mapped[list["OrganizationMember"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
    owned_namespaces: Mapped[list["Namespace"]] = relationship(
        back_populates="organization_owner"
    )


class OrganizationMember(Base):
    __tablename__ = "organization_members"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE")
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    role: Mapped[OrganizationRole] = mapped_column(
        SQLEnum(OrganizationRole), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    # Relationships
    organization: Mapped["Organization"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(back_populates="organization_memberships")

    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_organization_user"),
        Index("idx_organization_members_organization", "organization_id"),
        Index("idx_organization_members_user", "user_id"),
    )


class Namespace(Base):
    __tablename__ = "namespaces"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Owner can be either a User or Organization
    user_owner_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    organization_owner_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    user_owner: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[user_owner_id], back_populates="owned_namespaces"
    )
    organization_owner: Mapped[Optional["Organization"]] = relationship(
        "Organization",
        foreign_keys=[organization_owner_id],
        back_populates="owned_namespaces",
    )
    workflows: Mapped[list["Workflow"]] = relationship(
        back_populates="namespace", cascade="all, delete-orphan"
    )
    secrets: Mapped[list["Secret"]] = relationship(
        back_populates="namespace", cascade="all, delete-orphan"
    )
    providers: Mapped[list["Provider"]] = relationship(
        back_populates="namespace", cascade="all, delete-orphan"
    )

    @property
    def namespace_owner(self) -> Union["User", "Organization", None]:
        return self.user_owner or self.organization_owner

    __table_args__ = (
        CheckConstraint(
            "(user_owner_id IS NOT NULL)::int + (organization_owner_id IS NOT NULL)::int = 1",
            name="chk_namespace_single_owner",
        ),
        Index("idx_namespaces_user_owner", "user_owner_id"),
        Index("idx_namespaces_organization_owner", "organization_owner_id"),
    )


class Runtime(Base):
    __tablename__ = "runtimes"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    config: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    """
    {
        "image_hash": "...",  # sha256:xxx
    }
    """
    config_hash: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    creation_status: Mapped[RuntimeCreationStatus] = mapped_column(
        SQLEnum(RuntimeCreationStatus),
        nullable=False,
        default=RuntimeCreationStatus.IN_PROGRESS,
    )
    creation_logs: Mapped[Optional[list[dict]]] = mapped_column(JSONB, nullable=True)
    """
    [
        {
            "timestamp": "...",
            "message": "...",
            "level": "...",
        },
    ]
    """

    # Relationships
    deployments: Mapped[list["WorkflowDeployment"]] = relationship(
        back_populates="runtime"
    )

    __table_args__ = (UniqueConstraint("config_hash", name="uq_runtime_config_hash"),)


class Workflow(Base):
    __tablename__ = "workflows"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    namespace_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("namespaces.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )
    triggers_metadata: Mapped[Optional[list[dict]]] = mapped_column(
        JSONB, nullable=True
    )
    """
    [
        {
            "type": "webhook",
            "path": "/api/users",
            "method": "POST"
        },
        {
            "type": "cron",
            "expression": "0 0 * * *"
        }
    ]
    """

    # Relationships
    namespace: Mapped["Namespace"] = relationship(back_populates="workflows")
    created_by: Mapped[Optional["User"]] = relationship(
        back_populates="created_workflows"
    )
    deployments: Mapped[list["WorkflowDeployment"]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan"
    )
    triggers: Mapped[list["Trigger"]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("namespace_id", "name", name="uq_namespace_workflow"),
        Index("idx_workflows_namespace", "namespace_id"),
        Index("idx_workflows_created_by", "created_by_id"),
        Index("idx_workflows_updated_at", "updated_at"),
    )


class WorkflowDeployment(Base):
    __tablename__ = "workflow_deployments"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    workflow_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workflows.id", ondelete="CASCADE")
    )
    runtime_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("runtimes.id", ondelete="RESTRICT")
    )
    deployed_by_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    user_code: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    """
    {
        "files": {
            "main.ts": "...",
            "utils.ts": "...",
            ...
        },
        "entrypoint": "main.ts"
    }
    """
    deployed_at: Mapped[datetime] = mapped_column(server_default=func.now())
    status: Mapped[WorkflowDeploymentStatus] = mapped_column(
        SQLEnum(WorkflowDeploymentStatus),
        nullable=False,
        default=WorkflowDeploymentStatus.ACTIVE,
    )
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    workflow: Mapped["Workflow"] = relationship(back_populates="deployments")
    runtime: Mapped["Runtime"] = relationship(back_populates="deployments")
    deployed_by: Mapped[Optional["User"]] = relationship(
        foreign_keys=[deployed_by_id], back_populates="deployments"
    )

    __table_args__ = (
        Index("idx_workflow_deployments_workflow", "workflow_id"),
        Index("idx_workflow_deployments_status", "status"),
    )


class IncomingWebhook(Base):
    """
    Used to execute triggers based on an incoming webhooks

    Ex: new calendar events gets created which sends out webhook
    """

    __tablename__ = "incoming_webhooks"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    trigger_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("triggers.id", ondelete="CASCADE")
    )
    path: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(Text, nullable=False, server_default="POST")

    # Relationships
    trigger: Mapped["Trigger"] = relationship(back_populates="incoming_webhooks")


class RecurringTask(Base):
    """
    Used to execute triggers for this that don't have a webhooks

    Ex: check new calendar events every 5 minutes because the provider doesn't support webhook
    """

    __tablename__ = "recurring_tasks"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    trigger_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("triggers.id", ondelete="CASCADE")
    )

    # Relationships
    trigger: Mapped["Trigger"] = relationship(back_populates="recurring_tasks")


class Trigger(Base):
    __tablename__ = "triggers"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    workflow_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        # workflow should not be deleted to ensure proper cleanup of resources
        ForeignKey("workflows.id", ondelete="RESTRICT"),
    )
    provider_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        # provider should not be deleted to ensure proper cleanup of resources
        ForeignKey("providers.id", ondelete="RESTRICT"),
    )
    trigger_type: Mapped[str] = mapped_column(Text(), nullable=False)
    input: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=False)
    state: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Relationships
    workflow: Mapped["Workflow"] = relationship(back_populates="triggers")
    provider: Mapped["Provider"] = relationship(back_populates="triggers")
    incoming_webhooks: Mapped[list["IncomingWebhook"]] = relationship(
        back_populates="trigger", cascade="all, delete-orphan"
    )
    recurring_tasks: Mapped[list["RecurringTask"]] = relationship(
        back_populates="trigger", cascade="all, delete-orphan"
    )


class Secret(Base):
    __tablename__ = "secrets"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    namespace_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("namespaces.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(255), nullable=False)
    encrypted_value: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    namespace: Mapped["Namespace"] = relationship(back_populates="secrets")

    __table_args__ = (
        UniqueConstraint("namespace_id", "name", name="uq_namespace_secret"),
        Index("idx_secrets_namespace", "namespace_id"),
    )


class Provider(Base):
    __tablename__ = "providers"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    namespace_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("namespaces.id", ondelete="CASCADE")
    )
    type: Mapped[str] = mapped_column(Text(), nullable=False)
    alias: Mapped[str] = mapped_column(Text(), nullable=False)
    encrypted_config: Mapped[str] = mapped_column(Text, nullable=False)

    # Relationships
    namespace: Mapped["Namespace"] = relationship(back_populates="providers")
    triggers: Mapped[list["Trigger"]] = relationship(back_populates="provider")
