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


class WebhookListenerType(Enum):
    PUBLISHED_WORKFLOW = "published_workflow"
    LOCAL_WORKFLOW = "local_workflow"


class OrganizationRole(Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class NamespaceRole(Enum):
    OWNER = "owner"
    ADMIN = "admin"
    WRITE = "write"
    READ = "read"


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
    namespace_memberships: Mapped[list["NamespaceMember"]] = relationship(
        back_populates="user"
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
    members: Mapped[list["NamespaceMember"]] = relationship(
        back_populates="namespace", cascade="all, delete-orphan"
    )
    workflows: Mapped[list["Workflow"]] = relationship(
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


class NamespaceMember(Base):
    __tablename__ = "namespace_members"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    namespace_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("namespaces.id", ondelete="CASCADE")
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    role: Mapped[NamespaceRole] = mapped_column(SQLEnum(NamespaceRole), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    # Relationships
    namespace: Mapped["Namespace"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(back_populates="namespace_memberships")

    __table_args__ = (
        UniqueConstraint("namespace_id", "user_id", name="uq_namespace_user"),
        Index("idx_namespace_members_namespace", "namespace_id"),
        Index("idx_namespace_members_user", "user_id"),
    )


class Runtime(Base):
    __tablename__ = "runtimes"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    config: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    # Relationships
    deployments: Mapped[list["WorkflowDeployment"]] = relationship(
        back_populates="runtime"
    )

    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_runtime_name_version"),
    )


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

    # Relationships
    namespace: Mapped["Namespace"] = relationship(back_populates="workflows")
    created_by: Mapped[Optional["User"]] = relationship(
        back_populates="created_workflows"
    )
    deployments: Mapped[list["WorkflowDeployment"]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan"
    )
    webhook_listeners: Mapped[list["WebhookListener"]] = relationship(
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
    __tablename__ = "incoming_webhooks"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )

    # Relationships
    listeners: Mapped[list["WebhookListener"]] = relationship(
        back_populates="webhook", cascade="all, delete-orphan"
    )


class WebhookListener(Base):
    __tablename__ = "incoming_webhook_listeners"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    webhook_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("incoming_webhooks.id", ondelete="CASCADE")
    )
    workflow_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workflows.id", ondelete="CASCADE")
    )
    listener_type: Mapped[WebhookListenerType] = mapped_column(
        SQLEnum(WebhookListenerType), nullable=False
    )

    # Relationships
    webhook: Mapped["IncomingWebhook"] = relationship(back_populates="listeners")
    workflow: Mapped["Workflow"] = relationship(back_populates="webhook_listeners")
