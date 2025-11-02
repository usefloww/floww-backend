from typing import Any

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

_jwks_cache: dict[str, dict] = {}


async def fetch_jwks(jwks_url: str) -> dict:
    if jwks_url in _jwks_cache:
        return _jwks_cache[jwks_url]

    async with httpx.AsyncClient() as client:
        response = await client.get(jwks_url)
        response.raise_for_status()
        jwks = response.json()

    _jwks_cache[jwks_url] = jwks
    return jwks


def find_public_key(jwks: dict, key_id: str) -> Any:
    if not key_id:
        raise jwt.PyJWTError("No key ID found in JWT header")

    for key in jwks.get("keys", []):
        if key.get("kid") == key_id:
            return RSAAlgorithm.from_jwk(key)

    raise jwt.PyJWTError("No matching public key found")


def decode_and_validate_jwt(
    token: str, public_key: Any, issuer: str, audience: str, algorithms: list[str]
) -> dict:
    return jwt.decode(
        token, public_key, audience=audience, algorithms=algorithms, issuer=issuer
    )


def get_key_id_from_token(token: str) -> str:
    unverified_header = jwt.get_unverified_header(token)
    return unverified_header.get("kid")
