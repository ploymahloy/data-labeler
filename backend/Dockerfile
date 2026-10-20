FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY pyproject.toml ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
# RDS CA bundle for DB_SSL_MODE=verify-full (DB_SSL_CA_FILE=/srv/certs/rds-global-bundle.pem)
COPY certs ./certs

RUN pip install .

RUN useradd --system --uid 10001 labeler
USER 10001

EXPOSE 8000
# Keep-alive must outlast the ALB idle timeout (60s, see api-ingress.yaml). With uvicorn's 5s
# default the server closes idle connections the ALB still considers open, and the ALB then
# answers the next request on that connection with a sporadic 502.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--timeout-keep-alive", "75"]
