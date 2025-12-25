import urllib.parse

import httpx
import jwt
from fastapi import HTTPException, status
from jwt import algorithms

_discovery_cache: dict[str, dict] = {}
_jwks_cache: dict[str, dict] = {}


async def get_oidc_discovery(issuer_url: str) -> dict:
    if issuer_url in _discovery_cache:
        return _discovery_cache[issuer_url]

    discovery_url = f"{issuer_url.rstrip('/')}/.well-known/openid-configuration"

    async with httpx.AsyncClient() as client:
        response = await client.get(discovery_url, timeout=10.0)

        if response.status_code != 200:
            raise RuntimeError(
                f"Failed to fetch OIDC discovery from {discovery_url}: {response.status_code}"
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


async def validate_jwt(
    token: str,
    jwks_keys: list[dict],
    issuer: str,
    allowed_audiences: list[str | None],
    algorithm: str,
) -> dict:
    """
    Validate JWT token and return the full decoded payload.

    Args:
        token: JWT token to validate
        jwks_keys: List of JWKS keys from the JWKS endpoint
        issuer: Expected issuer to validate against
        allowed_audiences: Expected audiences (usually client IDs)
        algorithm: JWT algorithm to use for validation

    Returns:
        Full JWT payload as a dictionary

    Raises:
        HTTPException: If validation fails
    """
    try:
        unverified_header = jwt.get_unverified_header(token)
        key_id = unverified_header.get("kid")

        if not jwks_keys:
            raise jwt.PyJWTError("No keys found in JWKS")

        public_key = None

        if key_id:
            # If kid is present, find matching key
            for key in jwks_keys:
                if key.get("kid") == key_id:
                    public_key = algorithms.RSAAlgorithm.from_jwk(key)
                    break

            if not public_key:
                raise jwt.PyJWTError(f"No matching public key found for kid: {key_id}")
        else:
            # If kid is missing, use the first available key (common with single-key setups)
            try:
                public_key = algorithms.RSAAlgorithm.from_jwk(jwks_keys[0])
            except (IndexError, KeyError, ValueError) as e:
                raise jwt.PyJWTError(f"Failed to load public key from JWKS: {str(e)}")

        if not allowed_audiences:
            raise jwt.PyJWTError("No allowed audiences provided")

        payload = None

        # Decode the token without verifying audience to inspect its 'aud' claim
        unverified_payload = jwt.decode(
            token, options={"verify_signature": False, "verify_aud": False}
        )
        unverified_audience = unverified_payload.get("aud")

        last_audience_error = None
        for audience in allowed_audiences:
            try:
                payload = jwt.decode(
                    token,
                    public_key,
                    algorithms=[algorithm],
                    issuer=issuer,
                    audience=audience,
                )
                # If we succeed, break immediately
                break
            except jwt.PyJWTError as e:
                # Check if error is due to audience mismatch, continue to try others
                if "Audience doesn't match" in str(e):
                    last_audience_error = e
                    continue
                else:
                    # For all other errors, raise immediately
                    raise e
        else:
            # If we exhausted all audiences and no match, raise last audience error and show the unverified audience
            raise jwt.PyJWTError(
                f"Invalid audience. Token 'aud': {unverified_audience} | "
                f"{str(last_audience_error) if last_audience_error else 'No valid audience found'}"
            )

        # Validate that sub claim exists
        if payload.get("sub") is None:
            raise jwt.PyJWTError("No subject found in JWT payload")

        return payload

    except jwt.PyJWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {str(e)}",
        )


def get_authorization_url(
    authorization_endpoint: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    scope: str = "openid profile email",
    prompt: str | None = None,
) -> str:
    """Build OAuth authorization URL."""
    auth_params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
        "scope": scope,
    }

    if prompt:
        auth_params["prompt"] = prompt

    return f"{authorization_endpoint}?{urllib.parse.urlencode(auth_params)}"


async def exchange_code_for_token(
    token_endpoint: str,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> dict:
    """Exchange authorization code for tokens."""
    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            token_endpoint,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
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
