from sqlalchemy.orm import DeclarativeBase


from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, EmailStr, ConfigDict
from sqlalchemy import Boolean, String, Text, Integer, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


# SQLAlchemy Models
class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    # Relationships
    owned_namespaces: Mapped[list["Namespace"]] = relationship(back_populates="owner")
    namespace_memberships: Mapped[list["NamespaceMember"]] = relationship(back_populates="user")
    created_workflows: Mapped[list["Workflow"]] = relationship(back_populates="created_by")
    workflow_versions: Mapped[list["WorkflowVersion"]] = relationship(back_populates="created_by")
    deployments: Mapped[list["WorkflowDeployment"]] = relationship(
        foreign_keys="WorkflowDeployment.deployed_by_id", back_populates="deployed_by"
    )


class NamespaceRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    WRITE = "write"
    READ = "read"


class Namespace(Base):
    __tablename__ = "namespaces"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    is_organization: Mapped[bool] = mapped_column(Boolean, default=True)  # True for orgs, False for personal
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    # Relationships
    owner: Mapped["User"] = relationship(back_populates="owned_namespaces")
    members: Mapped[list["NamespaceMember"]] = relationship(back_populates="namespace", cascade="all, delete-orphan")
    workflows: Mapped[list["Workflow"]] = relationship(back_populates="namespace", cascade="all, delete-orphan")


class NamespaceMember(Base):
    __tablename__ = "namespace_members"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    namespace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("namespaces.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(50), nullable=False)  # owner, admin, write, read
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

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    config: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    # Relationships
    workflow_versions: Mapped[list["WorkflowVersion"]] = relationship(back_populates="runtime")

    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_runtime_name_version"),
    )


class Workflow(Base):
    __tablename__ = "workflows"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    namespace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("namespaces.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"))
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    # Relationships
    namespace: Mapped["Namespace"] = relationship(back_populates="workflows")
    created_by: Mapped["User"] = relationship(back_populates="created_workflows")
    versions: Mapped[list["WorkflowVersion"]] = relationship(back_populates="workflow", cascade="all, delete-orphan")
    deployments: Mapped[list["WorkflowDeployment"]] = relationship(back_populates="workflow", cascade="all, delete-orphan")
    tags: Mapped[list["WorkflowTag"]] = relationship(back_populates="workflow", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("namespace_id", "name", name="uq_namespace_workflow"),
        Index("idx_workflows_namespace", "namespace_id"),
        Index("idx_workflows_published", "is_published"),
        Index("idx_workflows_created_by", "created_by_id"),
    )


class WorkflowVersion(Base):
    __tablename__ = "workflow_versions"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    workflow_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("workflows.id", ondelete="CASCADE"))
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    runtime_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("runtimes.id"))
    code: Mapped[str] = mapped_column(Text, nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    commit_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    # Relationships
    workflow: Mapped["Workflow"] = relationship(back_populates="versions")
    runtime: Mapped["Runtime"] = relationship(back_populates="workflow_versions")
    created_by: Mapped["User"] = relationship(back_populates="workflow_versions")
    deployments: Mapped[list["WorkflowDeployment"]] = relationship(back_populates="version")
    tags: Mapped[list["WorkflowTag"]] = relationship(back_populates="version")

    __table_args__ = (
        UniqueConstraint("workflow_id", "version_number", name="uq_workflow_version"),
        Index("idx_workflow_versions_workflow", "workflow_id"),
        Index("idx_workflow_versions_created", "created_at"),
    )


class Environment(str, Enum):
    PRODUCTION = "production"
    STAGING = "staging"
    DEVELOPMENT = "development"


class WorkflowDeployment(Base):
    __tablename__ = "workflow_deployments"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    workflow_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("workflows.id", ondelete="CASCADE"))
    version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("workflow_versions.id"))
    environment: Mapped[str] = mapped_column(String(50), nullable=False, default=Environment.PRODUCTION.value)
    deployed_by_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"))
    deployed_at: Mapped[datetime] = mapped_column(server_default=func.now())
    rolled_back: Mapped[bool] = mapped_column(Boolean, default=False)
    rolled_back_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    rolled_back_by_id: Mapped[Optional[UUID]] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    workflow: Mapped["Workflow"] = relationship(back_populates="deployments")
    version: Mapped["WorkflowVersion"] = relationship(back_populates="deployments")
    deployed_by: Mapped["User"] = relationship(foreign_keys=[deployed_by_id], back_populates="deployments")
    rolled_back_by: Mapped[Optional["User"]] = relationship(foreign_keys=[rolled_back_by_id])

    __table_args__ = (
        Index("idx_workflow_deployments_workflow", "workflow_id"),
        Index("idx_workflow_deployments_active", "workflow_id", "environment", postgresql_where=(rolled_back == False)),
    )


class WorkflowTag(Base):
    __tablename__ = "workflow_tags"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    workflow_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("workflows.id", ondelete="CASCADE"))
    version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("workflow_versions.id"))
    tag_name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_by_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    # Relationships
    workflow: Mapped["Workflow"] = relationship(back_populates="tags")
    version: Mapped["WorkflowVersion"] = relationship(back_populates="tags")
    created_by: Mapped["User"] = relationship()

    __table_args__ = (
        UniqueConstraint("workflow_id", "tag_name", name="uq_workflow_tag"),
        Index("idx_workflow_tags_workflow", "workflow_id"),
    )