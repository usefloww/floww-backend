import time
from typing import Any, Dict
from uuid import UUID

import httpx
from pydantic import BaseModel
from structlog import get_logger

from app.settings import settings

logger = get_logger(__name__)


class WorkflowMessage(BaseModel):
    type: str
    workflow_id: str
    payload: Dict[str, Any]
    timestamp: str


class CentrifugoService:
    def __init__(self):
        self.api_key = settings.CENTRIFUGO_API_KEY
        self.host = settings.CENTRIFUGO_HOST
        self.port = settings.CENTRIFUGO_PORT

        # HTTP client for API calls
        self.client = httpx.AsyncClient(
            base_url=f"http://{self.host}:{self.port}",
            headers={"Authorization": f"apikey {self.api_key}"},
        )

    def get_workflow_channel(self, workflow_id: UUID) -> str:
        """Get the channel name for a specific workflow"""
        return f"workflow:{str(workflow_id)}"

    async def publish_to_channel(self, channel: str, data: Dict[str, Any]) -> bool:
        """Publish data to a Centrifugo channel via HTTP API"""
        try:
            payload = {
                "method": "publish",
                "params": {"channel": channel, "data": data},
            }

            response = await self.client.post("/api", json=payload)

            if response.status_code == 200:
                result = response.json()
                if result.get("error"):
                    logger.error(
                        f"Centrifugo API error: {result['error']}",
                        extra={"channel": channel, "error": result["error"]},
                    )
                    return False

                logger.info(
                    f"Successfully published to channel {channel}",
                    extra={
                        "channel": channel,
                        "data_type": data.get("type", "unknown"),
                    },
                )
                return True
            else:
                logger.error(
                    f"Failed to publish to channel {channel}: HTTP {response.status_code}",
                    extra={
                        "channel": channel,
                        "status_code": response.status_code,
                        "response": response.text,
                    },
                )
                return False

        except Exception as e:
            logger.error(
                "Error publishing to Centrifugo channel",
                extra={"channel": channel, "error": str(e)},
            )
            return False

    async def send_workflow_message(
        self, workflow_id: UUID, message_type: str, payload: Dict[str, Any]
    ) -> bool:
        """Send a message to a workflow channel"""
        channel = self.get_workflow_channel(workflow_id)

        message = WorkflowMessage(
            type=message_type,
            workflow_id=str(workflow_id),
            payload=payload,
            timestamp=str(time.time()),
        )

        return await self.publish_to_channel(channel, message.model_dump())

    async def notify_webhook_received(
        self, workflow_id: UUID, webhook_id: str, webhook_data: Dict[str, Any]
    ) -> bool:
        """Notify that a webhook was received for a workflow"""
        return await self.send_workflow_message(
            workflow_id=workflow_id,
            message_type="webhook.received",
            payload={"webhook_id": webhook_id, "data": webhook_data},
        )

    async def notify_workflow_execution_started(
        self, workflow_id: UUID, execution_id: str
    ) -> bool:
        """Notify that workflow execution has started"""
        return await self.send_workflow_message(
            workflow_id=workflow_id,
            message_type="workflow.execution.started",
            payload={"execution_id": execution_id},
        )

    async def notify_workflow_execution_completed(
        self, workflow_id: UUID, execution_id: str, result: Dict[str, Any]
    ) -> bool:
        """Notify that workflow execution has completed"""
        return await self.send_workflow_message(
            workflow_id=workflow_id,
            message_type="workflow.execution.completed",
            payload={"execution_id": execution_id, "result": result},
        )

    async def notify_workflow_execution_failed(
        self, workflow_id: UUID, execution_id: str, error: str
    ) -> bool:
        """Notify that workflow execution has failed"""
        return await self.send_workflow_message(
            workflow_id=workflow_id,
            message_type="workflow.execution.failed",
            payload={"execution_id": execution_id, "error": error},
        )

    async def close(self):
        """Close the HTTP client"""
        await self.client.aclose()


# Global instance
centrifugo_service = CentrifugoService()
