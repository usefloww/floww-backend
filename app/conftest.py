# pytest_plugins = [
#     "app.tests.fixtures_db",
#     "app.tests.fixtures_clients",
# ]

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tests.fixtures_billing import *  # noqa
from app.tests.fixtures_clients import *  # noqa
from app.tests.fixtures_db import *  # noqa


# Mock WorkOS functions to prevent real API calls during tests
@pytest.fixture(autouse=True)
def mock_workos_functions():
    """Mock all WorkOS API functions to prevent real API calls during tests."""

    # Mock organization for create
    mock_workos_org = MagicMock()
    mock_workos_org.id = "workos_org_test_123"

    # Mock invitation for send
    mock_invitation = MagicMock()
    mock_invitation.id = "workos_inv_test_123"
    mock_invitation.email = "test@example.com"
    mock_invitation.state = "pending"
    mock_invitation.created_at = "2025-01-01T00:00:00Z"
    mock_invitation.expires_at = "2025-01-08T00:00:00Z"

    # Mock invitations list
    mock_invitations_list = MagicMock()
    mock_invitations_list.data = []

    # Mock portal link
    mock_portal_link = MagicMock()
    mock_portal_link.link = "https://workos.example.com/portal/test"

    with (
        patch(
            "app.routes.organizations.create_workos_organization",
            new_callable=AsyncMock,
            return_value=mock_workos_org,
        ),
        patch(
            "app.routes.organizations.delete_workos_organization",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "app.routes.organizations.send_workos_invitation",
            new_callable=AsyncMock,
            return_value=mock_invitation,
        ),
        patch(
            "app.routes.organizations.list_workos_invitations",
            new_callable=AsyncMock,
            return_value=mock_invitations_list,
        ),
        patch(
            "app.routes.organizations.revoke_workos_invitation",
            new_callable=AsyncMock,
            return_value=mock_invitation,
        ),
        patch(
            "app.routes.organizations.generate_sso_portal_link",
            return_value=mock_portal_link,
        ),
    ):
        yield
