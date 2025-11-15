import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Optional, Union
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
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
from sqlalchemy.orm.exc import DetachedInstanceError
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    def __repr__(self) -> str:
        return self._repr(id=self.id)

    def _repr(self, **fields: dict[str, Any]) -> str:
        """
        Helper for __repr__
        """
        field_strings = []
        at_least_one_attached_attribute = False
        for key, field in fields.items():
            try:
                # Convert UUID fields to strings automatically
                if isinstance(field, UUID):
                    field = str(field)
                field_strings.append(f"{key}={field!r}")
            except DetachedInstanceError:
                field_strings.append(f"{key}=DetachedInstanceError")
            else:
                at_least_one_attached_attribute = True
        if at_least_one_attached_attribute:
            return f"<{self.__class__.__name__}({','.join(field_strings)})>"
        return f"<{self.__class__.__name__} {id(self)}>"


class WorkflowDeploymentStatus(Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    FAILED = "failed"


class RuntimeCreationStatus(Enum):
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class OrganizationRole(Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class UserType(str, Enum):
    HUMAN = "human"
    SERVICE_ACCOUNT = "service_account"


class ExecutionStatus(str, Enum):
    RECEIVED = "received"  # Webhook received, execution record created
    STARTED = "started"  # Runtime invocation initiated
    COMPLETED = "completed"  # Execution completed successfully
    FAILED = "failed"  # Execution failed with error
    TIMEOUT = "timeout"  # Execution timed out
    NO_DEPLOYMENT = "no_deployment"  # No active deployment found


class SubscriptionTier(str, Enum):
    FREE = "free"
    HOBBY = "hobby"


class SubscriptionStatus(str, Enum):
    ACTIVE = "active"  # Active paid subscription
    TRIALING = "trialing"  # In trial period
    PAST_DUE = "past_due"  # Payment failed but in grace period
    CANCELED = "canceled"  # Subscription canceled
    INCOMPLETE = "incomplete"  # Checkout session not completed


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_type: Mapped[UserType] = mapped_column(
        SQLEnum(UserType), nullable=False, default=UserType.HUMAN
    )
    workos_user_id: Mapped[Optional[str]] = mapped_column(
        String(255), unique=True, nullable=True
    )
    username: Mapped[Optional[str]] = mapped_column(
        String(255), unique=True, nullable=True
    )
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
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
    subscription: Mapped[Optional["Subscription"]] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )

    def __repr__(self):
        return self._repr(
            id=self.id, email=self.email, workos_user_id=self.workos_user_id
        )


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(
        String(255), unique=True, nullable=True
    )
    stripe_subscription_id: Mapped[Optional[str]] = mapped_column(
        String(255), unique=True, nullable=True
    )
    tier: Mapped[SubscriptionTier] = mapped_column(
        SQLEnum(SubscriptionTier), nullable=False, default=SubscriptionTier.FREE
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        SQLEnum(SubscriptionStatus), nullable=False, default=SubscriptionStatus.ACTIVE
    )
    trial_ends_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_period_end: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    grace_period_ends_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancel_at_period_end: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="subscription")
    billing_events: Mapped[list["BillingEvent"]] = relationship(
        back_populates="subscription", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("idx_subscriptions_user", "user_id"),)

    def __repr__(self):
        return self._repr(
            id=self.id, user_id=self.user_id, tier=self.tier, status=self.status
        )


class BillingEvent(Base):
    __tablename__ = "billing_events"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    subscription_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("subscriptions.id", ondelete="CASCADE")
    )
    event_type: Mapped[str] = mapped_column(String(255), nullable=False)
    stripe_event_id: Mapped[Optional[str]] = mapped_column(
        String(255), unique=True, nullable=True
    )
    payload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    subscription: Mapped["Subscription"] = relationship(back_populates="billing_events")

    __table_args__ = (
        Index("idx_billing_events_subscription", "subscription_id"),
        Index("idx_billing_events_event_type", "event_type"),
        Index("idx_billing_events_created_at", "created_at"),
    )

    def __repr__(self):
        return self._repr(
            id=self.id, subscription_id=self.subscription_id, event_type=self.event_type
        )


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    workos_organization_id: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=True
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
        back_populates="organization_owner", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return self._repr(id=self.id, name=self.name)


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


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    prefix: Mapped[str] = mapped_column(Text, nullable=False)
    """
    the prefix is used to show the user a small part of the api key to identify it
    it is not used for authentication. It shows the general prefix and then 3 characters of the actual key

    - floww_sa_xxx
    - floww_u_xxx
    """
    hashed_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    user_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("user_id", "prefix", name="uq_user_api_key_prefix"),
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
    kv_table_permissions: Mapped[list["KeyValueTablePermission"]] = relationship(
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

    A webhook can be owned by either a trigger or a provider, but not both.
    - Trigger-owned: webhook executes a specific trigger (e.g., GitLab merge request)
    - Provider-owned: webhook routes to all triggers for that provider (e.g., Slack workspace)
    """

    __tablename__ = "incoming_webhooks"
    __table_args__ = (
        CheckConstraint(
            "(trigger_id IS NOT NULL AND provider_id IS NULL) OR "
            "(trigger_id IS NULL AND provider_id IS NOT NULL)",
            name="webhook_owner_check",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    trigger_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("triggers.id", ondelete="CASCADE"),
        nullable=True,
    )
    provider_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("providers.id", ondelete="CASCADE"),
        nullable=True,
    )
    path: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(Text, nullable=False, server_default="POST")

    # Relationships
    trigger: Mapped[Optional["Trigger"]] = relationship(
        back_populates="incoming_webhooks"
    )
    provider: Mapped[Optional["Provider"]] = relationship(
        back_populates="incoming_webhooks"
    )


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
    incoming_webhooks: Mapped[list["IncomingWebhook"]] = relationship(
        back_populates="provider"
    )

    def __repr__(self):
        return self._repr(
            id=self.id, namespace_id=self.namespace_id, type=self.type, alias=self.alias
        )


class KeyValueTable(Base):
    __tablename__ = "kv_tables"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    provider_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("providers.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(Text(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    provider: Mapped["Provider"] = relationship()
    items: Mapped[list["KeyValueItem"]] = relationship(
        back_populates="table", cascade="all, delete-orphan"
    )
    permissions: Mapped[list["KeyValueTablePermission"]] = relationship(
        back_populates="table", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("provider_id", "name", name="uq_provider_table_name"),
        Index("idx_kv_tables_provider", "provider_id"),
    )

    def __repr__(self):
        return self._repr(id=self.id, provider_id=self.provider_id, name=self.name)


class KeyValueTablePermission(Base):
    __tablename__ = "kv_table_permissions"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    table_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("kv_tables.id", ondelete="CASCADE")
    )
    workflow_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workflows.id", ondelete="CASCADE")
    )
    can_read: Mapped[bool] = mapped_column(default=True)
    can_write: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    # Relationships
    table: Mapped["KeyValueTable"] = relationship(back_populates="permissions")
    workflow: Mapped["Workflow"] = relationship(back_populates="kv_table_permissions")

    __table_args__ = (
        UniqueConstraint(
            "table_id", "workflow_id", name="uq_table_workflow_permission"
        ),
        Index("idx_kv_permissions_table", "table_id"),
        Index("idx_kv_permissions_workflow", "workflow_id"),
    )

    def __repr__(self):
        return self._repr(
            id=self.id,
            table_id=self.table_id,
            workflow_id=self.workflow_id,
            can_read=self.can_read,
            can_write=self.can_write,
        )


class KeyValueItem(Base):
    __tablename__ = "kv_items"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    table_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("kv_tables.id", ondelete="CASCADE")
    )
    key: Mapped[str] = mapped_column(Text(), nullable=False)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    table: Mapped["KeyValueTable"] = relationship(back_populates="items")

    __table_args__ = (
        UniqueConstraint("table_id", "key", name="uq_table_key"),
        Index("idx_kv_items_table", "table_id"),
        Index("idx_kv_items_table_key", "table_id", "key"),
    )

    def __repr__(self):
        return self._repr(id=self.id, table_id=self.table_id, key=self.key)


class ExecutionHistory(Base):
    """
    Minimal execution history tracking for workflow invocations.
    All contextual data is retrieved via relationships to avoid duplication.
    """

    __tablename__ = "execution_history"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    workflow_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workflows.id", ondelete="CASCADE")
    )
    trigger_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("triggers.id", ondelete="SET NULL"),
        nullable=True,
    )
    deployment_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_deployments.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[ExecutionStatus] = mapped_column(
        SQLEnum(ExecutionStatus), nullable=False, default=ExecutionStatus.RECEIVED
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_stack: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    workflow: Mapped["Workflow"] = relationship("Workflow")
    trigger: Mapped[Optional["Trigger"]] = relationship("Trigger")
    deployment: Mapped[Optional["WorkflowDeployment"]] = relationship(
        "WorkflowDeployment"
    )

    __table_args__ = (
        Index("idx_execution_history_workflow", "workflow_id"),
        Index("idx_execution_history_trigger", "trigger_id"),
        Index("idx_execution_history_deployment", "deployment_id"),
        Index("idx_execution_history_status", "status"),
        Index("idx_execution_history_received_at", "received_at"),
        Index("idx_execution_history_workflow_status", "workflow_id", "status"),
        Index("idx_execution_history_workflow_received", "workflow_id", "received_at"),
    )

    def __repr__(self):
        return self._repr(id=self.id, workflow_id=self.workflow_id, status=self.status)
