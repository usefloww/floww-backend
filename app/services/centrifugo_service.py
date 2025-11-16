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

    async def publish_dev_webhook_event(
        self,
        workflow_id: UUID,
        trigger_metadata: Dict[str, Any],
        webhook_data: Dict[str, Any],
    ) -> None:
        """
        Publish webhook event to dev channel for local development.

        This is fire-and-forget - if no dev session is active (no subscribers),
        Centrifugo will drop the message. This keeps webhook handling fast.
        """
        channel = f"workflow:{str(workflow_id)}"

        event_data = {
            "type": "webhook",
            "auth_token": webhook_data.get("auth_token"),
            "path": webhook_data.get("path"),
            "method": webhook_data.get("method"),
            "headers": webhook_data.get("headers", {}),
            "body": webhook_data.get("body", {}),
            "query": webhook_data.get("query", {}),
            "trigger_metadata": trigger_metadata,
        }

        # Fire and forget - don't wait for response
        await self.publish_to_channel(channel, event_data)

    async def close(self):
        """Close the HTTP client"""
        await self.client.aclose()


# Global instance
centrifugo_service = CentrifugoService()
