"""GitHub action operations for Floww automation platform.

This module provides action operations for interacting with GitHub API:
- Create and update issues
- Create and update pull requests
- Add comments to issues/PRs
- Manage labels
- Update files in repositories
- Get repository information
"""

import base64
from typing import Any

import httpx
import structlog

logger = structlog.stdlib.get_logger(__name__)


class GitHubActionError(Exception):
    """Custom exception for GitHub action errors."""

    pass


class GitHubActions:
    """GitHub API action operations."""

    def __init__(self, access_token: str, server_url: str = "https://api.github.com"):
        """Initialize GitHub actions with authentication.

        Args:
            access_token: GitHub access token for authentication
            server_url: GitHub API server URL (default: https://api.github.com)
        """
        self.access_token = access_token
        self.server_url = server_url
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
        }

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        json_data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an authenticated request to GitHub API.

        Args:
            method: HTTP method (GET, POST, PUT, PATCH, DELETE)
            endpoint: API endpoint path
            json_data: JSON request body
            params: Query parameters

        Returns:
            Response data as dictionary

        Raises:
            GitHubActionError: If the request fails
        """
        url = f"{self.server_url}{endpoint}"

        try:
            async with httpx.AsyncClient() as client:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=self.headers,
                    json=json_data,
                    params=params,
                    timeout=30.0,
                )
                response.raise_for_status()

                # Handle 204 No Content responses
                if response.status_code == 204:
                    return {}

                return response.json()

        except httpx.HTTPStatusError as e:
            error_message = f"GitHub API request failed: {e.response.status_code}"
            try:
                error_data = e.response.json()
                error_message = f"{error_message} - {error_data.get('message', '')}"
            except Exception:
                pass

            logger.error(
                "GitHub API request failed",
                method=method,
                endpoint=endpoint,
                status_code=e.response.status_code,
                error=str(e),
            )
            raise GitHubActionError(error_message) from e

        except Exception as e:
            logger.error(
                "GitHub API request exception",
                method=method,
                endpoint=endpoint,
                error=str(e),
            )
            raise GitHubActionError(f"GitHub API request failed: {str(e)}") from e

    # Issue Operations

    async def create_issue(
        self,
        owner: str,
        repository: str,
        title: str,
        body: str | None = None,
        assignees: list[str] | None = None,
        labels: list[str] | None = None,
        milestone: int | None = None,
    ) -> dict[str, Any]:
        """Create a new issue in a repository.

        Args:
            owner: Repository owner (username or organization)
            repository: Repository name
            title: Issue title
            body: Issue body/description
            assignees: List of usernames to assign
            labels: List of label names to apply
            milestone: Milestone number to associate

        Returns:
            Created issue data
        """
        endpoint = f"/repos/{owner}/{repository}/issues"
        data: dict[str, Any] = {"title": title}

        if body is not None:
            data["body"] = body
        if assignees:
            data["assignees"] = assignees
        if labels:
            data["labels"] = labels
        if milestone is not None:
            data["milestone"] = milestone

        logger.info(
            "Creating GitHub issue", owner=owner, repository=repository, title=title
        )
        return await self._make_request("POST", endpoint, json_data=data)

    async def update_issue(
        self,
        owner: str,
        repository: str,
        issue_number: int,
        title: str | None = None,
        body: str | None = None,
        state: str | None = None,  # open or closed
        assignees: list[str] | None = None,
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        """Update an existing issue.

        Args:
            owner: Repository owner
            repository: Repository name
            issue_number: Issue number to update
            title: New issue title
            body: New issue body
            state: New state (open or closed)
            assignees: List of usernames to assign
            labels: List of label names to apply

        Returns:
            Updated issue data
        """
        endpoint = f"/repos/{owner}/{repository}/issues/{issue_number}"
        data: dict[str, Any] = {}

        if title is not None:
            data["title"] = title
        if body is not None:
            data["body"] = body
        if state is not None:
            data["state"] = state
        if assignees is not None:
            data["assignees"] = assignees
        if labels is not None:
            data["labels"] = labels

        logger.info(
            "Updating GitHub issue",
            owner=owner,
            repository=repository,
            issue_number=issue_number,
        )
        return await self._make_request("PATCH", endpoint, json_data=data)

    async def get_issue(
        self, owner: str, repository: str, issue_number: int
    ) -> dict[str, Any]:
        """Get an issue by number.

        Args:
            owner: Repository owner
            repository: Repository name
            issue_number: Issue number

        Returns:
            Issue data
        """
        endpoint = f"/repos/{owner}/{repository}/issues/{issue_number}"
        return await self._make_request("GET", endpoint)

    async def list_issues(
        self,
        owner: str,
        repository: str,
        state: str = "open",  # open, closed, all
        labels: list[str] | None = None,
        assignee: str | None = None,
        per_page: int = 30,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        """List issues in a repository.

        Args:
            owner: Repository owner
            repository: Repository name
            state: Issue state filter (open, closed, all)
            labels: Filter by labels
            assignee: Filter by assignee username
            per_page: Results per page (max 100)
            page: Page number

        Returns:
            List of issues
        """
        endpoint = f"/repos/{owner}/{repository}/issues"
        params: dict[str, Any] = {
            "state": state,
            "per_page": min(per_page, 100),
            "page": page,
        }

        if labels:
            params["labels"] = ",".join(labels)
        if assignee:
            params["assignee"] = assignee

        return await self._make_request("GET", endpoint, params=params)

    async def add_issue_comment(
        self, owner: str, repository: str, issue_number: int, body: str
    ) -> dict[str, Any]:
        """Add a comment to an issue or pull request.

        Args:
            owner: Repository owner
            repository: Repository name
            issue_number: Issue or PR number
            body: Comment body

        Returns:
            Created comment data
        """
        endpoint = f"/repos/{owner}/{repository}/issues/{issue_number}/comments"
        data = {"body": body}

        logger.info(
            "Adding comment to issue",
            owner=owner,
            repository=repository,
            issue_number=issue_number,
        )
        return await self._make_request("POST", endpoint, json_data=data)

    # Pull Request Operations

    async def create_pull_request(
        self,
        owner: str,
        repository: str,
        title: str,
        head: str,  # Branch name or username:branch
        base: str,  # Base branch name
        body: str | None = None,
        draft: bool = False,
    ) -> dict[str, Any]:
        """Create a new pull request.

        Args:
            owner: Repository owner
            repository: Repository name
            title: PR title
            head: Branch containing changes (format: username:branch or branch)
            base: Base branch for comparison
            body: PR description
            draft: Whether to create as draft PR

        Returns:
            Created pull request data
        """
        endpoint = f"/repos/{owner}/{repository}/pulls"
        data: dict[str, Any] = {
            "title": title,
            "head": head,
            "base": base,
            "draft": draft,
        }

        if body is not None:
            data["body"] = body

        logger.info(
            "Creating pull request",
            owner=owner,
            repository=repository,
            title=title,
            head=head,
            base=base,
        )
        return await self._make_request("POST", endpoint, json_data=data)

    async def update_pull_request(
        self,
        owner: str,
        repository: str,
        pull_number: int,
        title: str | None = None,
        body: str | None = None,
        state: str | None = None,  # open or closed
        base: str | None = None,
    ) -> dict[str, Any]:
        """Update an existing pull request.

        Args:
            owner: Repository owner
            repository: Repository name
            pull_number: Pull request number
            title: New PR title
            body: New PR body
            state: New state (open or closed)
            base: New base branch

        Returns:
            Updated pull request data
        """
        endpoint = f"/repos/{owner}/{repository}/pulls/{pull_number}"
        data: dict[str, Any] = {}

        if title is not None:
            data["title"] = title
        if body is not None:
            data["body"] = body
        if state is not None:
            data["state"] = state
        if base is not None:
            data["base"] = base

        logger.info(
            "Updating pull request",
            owner=owner,
            repository=repository,
            pull_number=pull_number,
        )
        return await self._make_request("PATCH", endpoint, json_data=data)

    async def merge_pull_request(
        self,
        owner: str,
        repository: str,
        pull_number: int,
        commit_title: str | None = None,
        commit_message: str | None = None,
        merge_method: str = "merge",  # merge, squash, rebase
    ) -> dict[str, Any]:
        """Merge a pull request.

        Args:
            owner: Repository owner
            repository: Repository name
            pull_number: Pull request number
            commit_title: Title for merge commit
            commit_message: Message for merge commit
            merge_method: Merge method (merge, squash, rebase)

        Returns:
            Merge result data
        """
        endpoint = f"/repos/{owner}/{repository}/pulls/{pull_number}/merge"
        data: dict[str, Any] = {"merge_method": merge_method}

        if commit_title is not None:
            data["commit_title"] = commit_title
        if commit_message is not None:
            data["commit_message"] = commit_message

        logger.info(
            "Merging pull request",
            owner=owner,
            repository=repository,
            pull_number=pull_number,
            merge_method=merge_method,
        )
        return await self._make_request("PUT", endpoint, json_data=data)

    async def get_pull_request(
        self, owner: str, repository: str, pull_number: int
    ) -> dict[str, Any]:
        """Get a pull request by number.

        Args:
            owner: Repository owner
            repository: Repository name
            pull_number: Pull request number

        Returns:
            Pull request data
        """
        endpoint = f"/repos/{owner}/{repository}/pulls/{pull_number}"
        return await self._make_request("GET", endpoint)

    async def list_pull_requests(
        self,
        owner: str,
        repository: str,
        state: str = "open",  # open, closed, all
        base: str | None = None,
        head: str | None = None,
        per_page: int = 30,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        """List pull requests in a repository.

        Args:
            owner: Repository owner
            repository: Repository name
            state: PR state filter (open, closed, all)
            base: Filter by base branch
            head: Filter by head branch (format: username:branch)
            per_page: Results per page (max 100)
            page: Page number

        Returns:
            List of pull requests
        """
        endpoint = f"/repos/{owner}/{repository}/pulls"
        params: dict[str, Any] = {
            "state": state,
            "per_page": min(per_page, 100),
            "page": page,
        }

        if base:
            params["base"] = base
        if head:
            params["head"] = head

        return await self._make_request("GET", endpoint, params=params)

    # File Operations

    async def get_file_content(
        self, owner: str, repository: str, path: str, ref: str | None = None
    ) -> dict[str, Any]:
        """Get the contents of a file.

        Args:
            owner: Repository owner
            repository: Repository name
            path: File path
            ref: Git reference (branch, tag, commit SHA)

        Returns:
            File content data including content, sha, and metadata
        """
        endpoint = f"/repos/{owner}/{repository}/contents/{path}"
        params = {"ref": ref} if ref else {}
        return await self._make_request("GET", endpoint, params=params)

    async def create_or_update_file(
        self,
        owner: str,
        repository: str,
        path: str,
        message: str,
        content: str,
        branch: str | None = None,
        sha: str | None = None,  # Required for updates
    ) -> dict[str, Any]:
        """Create or update a file in a repository.

        Args:
            owner: Repository owner
            repository: Repository name
            path: File path
            message: Commit message
            content: File content (will be base64 encoded)
            branch: Branch name (defaults to default branch)
            sha: File SHA (required for updates, get from get_file_content)

        Returns:
            Commit data
        """
        endpoint = f"/repos/{owner}/{repository}/contents/{path}"

        # Encode content to base64
        content_bytes = content.encode("utf-8")
        content_base64 = base64.b64encode(content_bytes).decode("utf-8")

        data: dict[str, Any] = {
            "message": message,
            "content": content_base64,
        }

        if branch:
            data["branch"] = branch
        if sha:
            data["sha"] = sha

        logger.info(
            "Creating/updating file",
            owner=owner,
            repository=repository,
            path=path,
            branch=branch,
        )
        return await self._make_request("PUT", endpoint, json_data=data)

    async def delete_file(
        self,
        owner: str,
        repository: str,
        path: str,
        message: str,
        sha: str,
        branch: str | None = None,
    ) -> dict[str, Any]:
        """Delete a file from a repository.

        Args:
            owner: Repository owner
            repository: Repository name
            path: File path
            message: Commit message
            sha: File SHA (get from get_file_content)
            branch: Branch name (defaults to default branch)

        Returns:
            Commit data
        """
        endpoint = f"/repos/{owner}/{repository}/contents/{path}"
        data: dict[str, Any] = {
            "message": message,
            "sha": sha,
        }

        if branch:
            data["branch"] = branch

        logger.info(
            "Deleting file",
            owner=owner,
            repository=repository,
            path=path,
            branch=branch,
        )
        return await self._make_request("DELETE", endpoint, json_data=data)

    # Label Operations

    async def add_labels_to_issue(
        self, owner: str, repository: str, issue_number: int, labels: list[str]
    ) -> list[dict[str, Any]]:
        """Add labels to an issue or pull request.

        Args:
            owner: Repository owner
            repository: Repository name
            issue_number: Issue or PR number
            labels: List of label names to add

        Returns:
            List of labels on the issue
        """
        endpoint = f"/repos/{owner}/{repository}/issues/{issue_number}/labels"
        data = {"labels": labels}

        logger.info(
            "Adding labels to issue",
            owner=owner,
            repository=repository,
            issue_number=issue_number,
            labels=labels,
        )
        return await self._make_request("POST", endpoint, json_data=data)

    async def remove_label_from_issue(
        self, owner: str, repository: str, issue_number: int, label: str
    ) -> None:
        """Remove a label from an issue or pull request.

        Args:
            owner: Repository owner
            repository: Repository name
            issue_number: Issue or PR number
            label: Label name to remove
        """
        endpoint = f"/repos/{owner}/{repository}/issues/{issue_number}/labels/{label}"

        logger.info(
            "Removing label from issue",
            owner=owner,
            repository=repository,
            issue_number=issue_number,
            label=label,
        )
        await self._make_request("DELETE", endpoint)

    async def create_label(
        self,
        owner: str,
        repository: str,
        name: str,
        color: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Create a new label in a repository.

        Args:
            owner: Repository owner
            repository: Repository name
            name: Label name
            color: Label color (hex code without #)
            description: Label description

        Returns:
            Created label data
        """
        endpoint = f"/repos/{owner}/{repository}/labels"
        data: dict[str, Any] = {
            "name": name,
            "color": color,
        }

        if description is not None:
            data["description"] = description

        logger.info(
            "Creating label",
            owner=owner,
            repository=repository,
            name=name,
        )
        return await self._make_request("POST", endpoint, json_data=data)

    # Repository Operations

    async def get_repository(self, owner: str, repository: str) -> dict[str, Any]:
        """Get repository information.

        Args:
            owner: Repository owner
            repository: Repository name

        Returns:
            Repository data
        """
        endpoint = f"/repos/{owner}/{repository}"
        return await self._make_request("GET", endpoint)

    async def list_repository_branches(
        self, owner: str, repository: str, per_page: int = 30, page: int = 1
    ) -> list[dict[str, Any]]:
        """List branches in a repository.

        Args:
            owner: Repository owner
            repository: Repository name
            per_page: Results per page (max 100)
            page: Page number

        Returns:
            List of branches
        """
        endpoint = f"/repos/{owner}/{repository}/branches"
        params = {
            "per_page": min(per_page, 100),
            "page": page,
        }
        return await self._make_request("GET", endpoint, params=params)

    async def list_repository_tags(
        self, owner: str, repository: str, per_page: int = 30, page: int = 1
    ) -> list[dict[str, Any]]:
        """List tags in a repository.

        Args:
            owner: Repository owner
            repository: Repository name
            per_page: Results per page (max 100)
            page: Page number

        Returns:
            List of tags
        """
        endpoint = f"/repos/{owner}/{repository}/tags"
        params = {
            "per_page": min(per_page, 100),
            "page": page,
        }
        return await self._make_request("GET", endpoint, params=params)
