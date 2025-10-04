###########
# Builder #
###########
FROM registry.gitlab.com/techwolfbe/infrastructure/base-images/python-313-builder as builder

COPY ./requirements/requirements_app.txt requirements.txt
RUN --mount=type=secret,id=package_registry_token,uid=1000 \
    --mount=type=cache,target=/home/appuser/.cache/uv,uid=1000,gid=2000 \
    GL_PAT=$(cat /run/secrets/package_registry_token) uv pip install -r requirements.txt


##########
# Checks #
##########

FROM builder AS checks

COPY ./requirements/additional_requirements_mypy.txt ./requirements/additional_requirements_test.txt ./
RUN --mount=type=cache,target=/home/appuser/.cache/uv,uid=1000,gid=2000 \
    uv pip install -r additional_requirements_mypy.txt -r additional_requirements_test.txt

###############
# Application #
###############
FROM registry.gitlab.com/techwolfbe/infrastructure/base-images/python-313 as app
COPY --from=builder /home/appuser/venv /home/appuser/venv

COPY ./src /home/appuser/src
WORKDIR /home/appuser/src

CMD ["python", "-m", "gunicorn", "-k", "uvicorn.workers.UvicornWorker", "main:app", "--bind", "0.0.0.0:8000", "--workers", "2"]

