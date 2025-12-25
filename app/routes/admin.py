import httpx
from fastapi import FastAPI, Request
from fastapi.responses import Response
from sqladmin import Admin

from app.admin.admin_auth import AdminAuth
from app.admin.admin_model_views import ALL_VIEWS
from app.deps.db import AsyncSessionLocal
from app.settings import settings


async def _check_admin_auth(request: Request):
    """Check if user is authenticated as admin, return redirect response if not."""
    admin_auth = AdminAuth()
    auth_result = await admin_auth.authenticate(request)

    # If auth_result is True, user is authenticated
    if auth_result is True:
        return None

    # Otherwise, auth_result is a Response (redirect or error)
    return auth_result


async def _centrifugo_proxy(request: Request):
    # Check authentication first
    auth_response = await _check_admin_auth(request)
    if auth_response is not None:
        return auth_response

    async with httpx.AsyncClient() as client:
        # Construct the target URL
        target_url = f"http://{settings.CENTRIFUGO_HOST}:{settings.CENTRIFUGO_PORT}"

        # Get the path and query parameters from the original request
        path = request.url.path.replace("/admin/centrifugo", "")
        if not path:
            path = "/"

        # Build the complete URL
        url = f"{target_url}{path}"
        if request.url.query:
            url = f"{url}?{request.url.query}"

        # Forward the request
        response = await client.request(
            method=request.method,
            url=url,
            headers=dict(request.headers),
            content=await request.body(),
        )

        # Return the response from Centrifugo
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=dict(response.headers),
        )


async def _links_page(request: Request):
    # Check authentication first
    auth_response = await _check_admin_auth(request)
    if auth_response is not None:
        return auth_response

    return Response(
        content="""
    <html>
        <body>
            <h1>Links</h1>
            
            <ul>
                <li><a href="/admin/api/">API</a></li>
                <li><a href="/admin/centrifugo/#/">Centrifugo dashboard</a></li>
            </ul>
        </body>
    </html>
    """,
        headers={"Content-Type": "text/html"},
        status_code=200,
    )


def init_admin(app: FastAPI):
    admin = Admin(
        app=app,
        session_maker=AsyncSessionLocal,
        base_url="/admin/api",
        authentication_backend=AdminAuth(),
    )

    for view in ALL_VIEWS:
        admin.add_view(view)

    app.add_api_route("/admin", _links_page, methods=["GET"])

    app.add_api_route(
        "/admin/centrifugo/{path:path}",
        _centrifugo_proxy,
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
    )
    app.add_api_route(
        "/admin/centrifugo",
        _centrifugo_proxy,
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
    )
