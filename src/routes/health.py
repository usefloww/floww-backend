import os
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from typing_extensions import TypedDict

router = APIRouter(tags=["Health"])


class HealthResponse(TypedDict):
    status: Literal["pass"]


REQUIRED_VARIABLES = ()


@router.get("/health", responses={200: {"model": HealthResponse}})
async def health():
    missing_environment_variables = []

    for variable in REQUIRED_VARIABLES:
        if variable not in os.environ:
            missing_environment_variables.append(variable)

    if len(missing_environment_variables) != 0:
        return JSONResponse(
            status_code=500,
            content={
                "status": "fail",
                "missing_environment_variables": missing_environment_variables,
            },
        )

    return {"status": "pass"}
