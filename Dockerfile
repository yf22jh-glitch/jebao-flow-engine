FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system jebao-flow \
    && adduser --system --ingroup jebao-flow --home /nonexistent jebao-flow

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m pip install --no-cache-dir .

RUN mkdir -p /config /data \
    && chown -R jebao-flow:jebao-flow /config /data

USER jebao-flow
VOLUME ["/config", "/data"]

ENTRYPOINT ["jebao-flowd"]
CMD ["--config", "/config/config.yaml"]

