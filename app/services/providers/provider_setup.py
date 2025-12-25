from abc import ABC
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


# base class for all setup steps
class ProviderSetupStep(BaseModel, ABC):
    type: Literal["value", "secret", "oauth", "choice", "file", "info", "webhook"] = (
        Field(..., description="Type of setup step")
    )


# simple text / url / number input
class ProviderSetupStepValue(ProviderSetupStep):
    type: Literal["value"] = "value"
    title: str
    description: str
    alias: str
    default: Optional[str] = None
    placeholder: Optional[str] = None
    required: bool = True


# secret input (passwords, tokens)
class ProviderSetupStepSecret(ProviderSetupStep):
    type: Literal["secret"] = "secret"
    title: str
    description: str
    alias: str
    placeholder: Optional[str] = None
    required: bool = True


# oauth-based step
class ProviderSetupStepOAuth(ProviderSetupStep):
    type: Literal["oauth"] = "oauth"
    provider_name: str
    scopes: List[str]
    redirect_uri: str


# user selects one from predefined options
class ProviderSetupStepChoice(ProviderSetupStep):
    type: Literal["choice"] = "choice"
    title: str
    description: str
    alias: str
    options: List[str]
    default: Optional[str] = None


# upload or reference a file
class ProviderSetupStepFile(ProviderSetupStep):
    type: Literal["file"] = "file"
    title: str
    description: str
    alias: str
    required: bool = True


# purely informational / confirmation step
class ProviderSetupStepInfo(ProviderSetupStep):
    type: Literal["info"] = "info"
    title: str
    message: str
    action_text: Optional[str] = None  # e.g. “Open dashboard”
    action_url: Optional[str] = None


class ProviderSetupStepWebhook(ProviderSetupStep):
    type: Literal["webhook"] = "webhook"
    title: str
    description: str
    alias: str
    default: Optional[str] = None
    required: bool = True


# example provider definition


class GoogleCalendarProvider(BaseModel):
    name: str = "google_calendar"
    setup_steps: List[ProviderSetupStep] = [
        ProviderSetupStepOAuth(
            provider_name="google",
            scopes=["calendar.readonly"],
            redirect_uri="https://yourapp.com/oauth/callback",
        ),
        ProviderSetupStepInfo(
            title="Authorize Access",
            message="Go to your Google account and verify calendar permissions.",
        ),
    ]
