from typing import Union
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app.deps.db import SessionDep
from app.models import (
    Namespace,
    OrganizationMember,
    Runtime,
    Secret,
    Workflow,
    WorkflowDeployment,
)


class UserAccessibleQuery:
    def __init__(self, user_id: UUID):
        self.user_id = user_id

    def namespaces(self):
        return select(Namespace).where(
            or_(
                Namespace.user_owner_id == str(self.user_id),
                Namespace.organization_owner.has(
                    OrganizationMember.user_id == str(self.user_id)
                ),
            )
        )

    def workflows(self):
        return select(Workflow).where(
            Workflow.namespace.has(
                or_(
                    Namespace.user_owner_id == str(self.user_id),
                    Namespace.organization_owner.has(
                        OrganizationMember.user_id == str(self.user_id)
                    ),
                )
            )
        )

    def deployments(self):
        return select(WorkflowDeployment).where(
            WorkflowDeployment.workflow.has(
                or_(
                    Namespace.user_owner_id == str(self.user_id),
                    Namespace.organization_owner.has(
                        OrganizationMember.user_id == str(self.user_id)
                    ),
                )
            )
        )

    def secrets(self):
        return select(Secret).where(
            Secret.namespace.has(
                or_(
                    Namespace.user_owner_id == str(self.user_id),
                    Namespace.organization_owner.has(
                        OrganizationMember.user_id == str(self.user_id)
                    ),
                )
            )
        )

    def runtimes(self):
        return select(Runtime).where()


async def get_workflow_or_404(
    session: SessionDep, workflow_id: Union[str, UUID]
) -> Workflow:
    """Get a workflow by ID or raise 404 if not found."""
    result = await session.execute(
        select(Workflow)
        .options(selectinload(Workflow.namespace))
        .where(Workflow.id == str(workflow_id))
    )
    workflow = result.scalar_one_or_none()

    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    return workflow


async def get_runtime_or_404(
    session: SessionDep, runtime_id: Union[str, UUID]
) -> Runtime:
    """Get a runtime by ID or raise 404 if not found."""
    result = await session.execute(select(Runtime).where(Runtime.id == str(runtime_id)))
    runtime = result.scalar_one_or_none()

    if not runtime:
        raise HTTPException(status_code=404, detail="Runtime not found")

    return runtime


async def get_deployment_or_404(
    session: SessionDep, deployment_id: Union[str, UUID]
) -> WorkflowDeployment:
    """Get a workflow deployment by ID or raise 404 if not found."""
    result = await session.execute(
        select(WorkflowDeployment)
        .options(
            selectinload(WorkflowDeployment.workflow).selectinload(Workflow.namespace),
            selectinload(WorkflowDeployment.runtime),
            selectinload(WorkflowDeployment.deployed_by),
        )
        .where(WorkflowDeployment.id == str(deployment_id))
    )
    deployment = result.scalar_one_or_none()

    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")

    return deployment


def get_workflow_with_namespace_options():
    """Get standard selectinload options for workflow with namespace."""
    return selectinload(Workflow.namespace)


def get_deployment_with_relations_options():
    """Get standard selectinload options for deployment with all relations."""
    return [
        selectinload(WorkflowDeployment.workflow).selectinload(Workflow.namespace),
        selectinload(WorkflowDeployment.runtime),
        selectinload(WorkflowDeployment.deployed_by),
    ]
