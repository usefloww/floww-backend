ARG UID=1000
ARG GID=2000

FROM ghcr.io/astral-sh/uv:0.8.4 AS uv

###########
# Builder #
###########
FROM python:3.13-slim-bookworm AS builder
ARG UID
ARG GID

RUN rm /var/lib/dpkg/info/libc-bin.* && \
    apt-get clean && \
    apt-get update && \
    apt-get install -y libc-bin
RUN apt-get update && \
    apt-get -y upgrade && \
    apt-get install -y --no-install-recommends build-essential && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd -g "${GID}" appuser
RUN useradd -u "${UID}" -g "${GID}" --create-home -s /bin/bash appuser

COPY --chown=appuser:appuser --from=uv /uv /usr/local/bin/uv
USER appuser
ENV PATH="/home/appuser/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 VIRTUAL_ENV=/home/appuser/venv
WORKDIR /home/appuser

RUN uv venv /home/appuser/venv
RUN uv pip install setuptools wheel pip

COPY ./requirements/requirements_app.txt requirements.txt
RUN --mount=type=cache,target=/home/appuser/.cache/uv,uid=1000,gid=2000 \
    uv pip install -r requirements.txt

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
FROM python:3.13-slim-bookworm AS app
ARG UID=1000
ARG GID=2000

RUN groupadd -g "${GID}" appuser
RUN useradd -u "${UID}" -g "${GID}" --create-home -s /bin/bash appuser

USER appuser
ENV PATH="/home/appuser/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 VIRTUAL_ENV=/home/appuser/venv
WORKDIR /home/appuser

COPY --from=builder /home/appuser/venv /home/appuser/venv
COPY ./app /home/appuser/app
COPY ./alembic.ini /home/appuser/alembic.ini
WORKDIR /home/appuser

CMD ["python", "-m", "gunicorn", "-k", "app.utils.uvicorn_worker.MyUvicornWorker", "app.main:app", "--bind", "0.0.0.0:8000", "--workers", "2"]

