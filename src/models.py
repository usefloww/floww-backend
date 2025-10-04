from datetime import datetime
from enum import Enum
from typing import Optional, Union
from uuid import UUID, uuid4

from sqlalchemy import (
    String,
    Text,
    ForeignKey,
    Index,
    UniqueConstraint,
    CheckConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class WorkflowDeploymentStatus(Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    FAILED = "failed"


# SQLAlchemy Models
class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    username: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
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
    role: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # owner, admin, member
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
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    organization_owner_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True
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
            "(user_owner_id IS NOT NULL AND organization_owner_id IS NULL) OR "
            "(user_owner_id IS NULL AND organization_owner_id IS NOT NULL)",
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
    role: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # owner, admin, write, read
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
    deployments: Mapped[list["WorkflowDeployment"]] = relationship(back_populates="runtime")

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
    created_by_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    namespace: Mapped["Namespace"] = relationship(back_populates="workflows")
    created_by: Mapped["User"] = relationship(back_populates="created_workflows")
    deployments: Mapped[list["WorkflowDeployment"]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("namespace_id", "name", name="uq_namespace_workflow"),
        Index("idx_workflows_namespace", "namespace_id"),
        Index("idx_workflows_created_by", "created_by_id"),
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
        PGUUID(as_uuid=True), ForeignKey("runtimes.id")
    )
    deployed_by_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id")
    )
    deployed_at: Mapped[datetime] = mapped_column(server_default=func.now())
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=WorkflowDeploymentStatus.ACTIVE.value
    )
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    workflow: Mapped["Workflow"] = relationship(back_populates="deployments")
    runtime: Mapped["Runtime"] = relationship(back_populates="deployments")
    deployed_by: Mapped["User"] = relationship(
        foreign_keys=[deployed_by_id], back_populates="deployments"
    )

    __table_args__ = (Index("idx_workflow_deployments_workflow", "workflow_id"),)
