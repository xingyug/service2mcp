FROM python:3.11-slim

ARG APP_MODULE
ARG APP_PORT=8000
ARG INSTALL_EXTRAS=extractors,enhancer,observability

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV APP_MODULE=${APP_MODULE}
ENV APP_PORT=${APP_PORT}
ENV INSTALL_EXTRAS=${INSTALL_EXTRAS}

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY README.md /app/README.md
COPY apps /app/apps
COPY libs /app/libs
COPY migrations /app/migrations
COPY tests/fixtures /app/tests/fixtures

RUN pip install --upgrade pip && pip install ".[${INSTALL_EXTRAS}]"

CMD ["sh", "-lc", "python -m uvicorn ${APP_MODULE} --host 0.0.0.0 --port ${APP_PORT}"]
