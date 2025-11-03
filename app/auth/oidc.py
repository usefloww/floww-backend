import urllib.parse

import httpx
import jwt
from fastapi import HTTPException, status

from app.settings import settings

_discovery_cache: dict[str, dict] = {}
_jwks_cache: dict[str, dict] = {}


async def get_oidc_discovery(issuer_url: str) -> dict:
    if issuer_url in _discovery_cache:
        return _discovery_cache[issuer_url]

    discovery_url = f"{issuer_url.rstrip('/')}/.well-known/openid-configuration"

    async with httpx.AsyncClient() as client:
        response = await client.get(discovery_url, timeout=10.0)

        if response.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch OIDC discovery from {discovery_url}",
            )

        _discovery_cache[issuer_url] = response.json()
        return _discovery_cache[issuer_url]


async def get_jwks(jwks_url: str) -> dict:
    if jwks_url in _jwks_cache:
        return _jwks_cache[jwks_url]

    async with httpx.AsyncClient() as client:
        response = await client.get(jwks_url)
        response.raise_for_status()
        jwks = response.json()

    _jwks_cache[jwks_url] = jwks
    return jwks


async def validate_jwt(token: str, issuer_url: str, audience: str) -> str:
    """
    Validate JWT token using OIDC discovery and return the user ID (sub claim).

    Args:
        token: JWT token to validate
        issuer_url: OIDC issuer URL
        audience: Expected audience (usually client ID)

    Returns:
        User ID from the 'sub' claim

    Raises:
        HTTPException: If validation fails
    """
    try:
        discovery = await get_oidc_discovery(issuer_url)
        jwks_url = discovery.get("jwks_uri")

        if not jwks_url:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="JWKS URI not found in OIDC discovery",
            )

        unverified_header = jwt.get_unverified_header(token)
        key_id = unverified_header.get("kid")

        jwks = await get_jwks(jwks_url)
        keys = jwks.get("keys", [])

        if not keys:
            raise jwt.PyJWTError("No keys found in JWKS")

        public_key = None

        if key_id:
            # If kid is present, find matching key
            for key in keys:
                if key.get("kid") == key_id:
                    public_key = jwt.algorithms.RSAAlgorithm.from_jwk(key)
                    break

            if not public_key:
                raise jwt.PyJWTError(f"No matching public key found for kid: {key_id}")
        else:
            # If kid is missing, use the first available key (common with single-key setups)
            try:
                public_key = jwt.algorithms.RSAAlgorithm.from_jwk(keys[0])
            except (IndexError, KeyError, ValueError) as e:
                raise jwt.PyJWTError(f"Failed to load public key from JWKS: {str(e)}")

        issuer = discovery.get("issuer")

        issuer = f"https://api.workos.com/user_management/{settings.AUTH_CLIENT_ID}"
        if not issuer:
            issuer = issuer_url

        payload = jwt.decode(
            token,
            public_key,
            algorithms=[settings.JWT_ALGORITHM],
            issuer=issuer,
            # audience=audience,
        )

        external_user_id: str = payload.get("sub")
        if external_user_id is None:
            raise jwt.PyJWTError("No subject found in JWT payload")

        return external_user_id

    except jwt.PyJWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {str(e)}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Token validation error: {str(e)}",
        )


async def get_authorization_url(
    redirect_uri: str, state: str, scope: str = "openid profile email"
) -> str:
    """Build OAuth authorization URL using OIDC discovery."""
    discovery = await get_oidc_discovery(settings.AUTH_ISSUER_URL)
    auth_endpoint = discovery.get("authorization_endpoint")

    if not auth_endpoint:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authorization endpoint not found in OIDC discovery",
        )

    auth_params = {
        "client_id": settings.AUTH_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
        "scope": scope,
    }

    return f"{auth_endpoint}?{urllib.parse.urlencode(auth_params)}"


async def exchange_code_for_token(code: str, redirect_uri: str) -> dict:
    """Exchange authorization code for tokens using OIDC token endpoint."""
    discovery = await get_oidc_discovery(settings.AUTH_ISSUER_URL)
    token_endpoint = discovery.get("token_endpoint")

    if not token_endpoint:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Token endpoint not found in OIDC discovery",
        )

    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            token_endpoint,
            data={
                "client_id": settings.AUTH_CLIENT_ID,
                "client_secret": settings.AUTH_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if token_response.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to exchange code for token: {token_response.text}",
            )

        return token_response.json()
