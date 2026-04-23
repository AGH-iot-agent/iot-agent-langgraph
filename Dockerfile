FROM python:3.12-slim AS base

RUN groupadd --gid 1000 app && \
    useradd --uid 1000 --gid app --shell /bin/sh --create-home app

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[all]" 2>/dev/null || pip install --no-cache-dir \
    "fastapi>=0.115.0" \
    "uvicorn>=0.30.0" \
    "langgraph>=0.2.0" \
    "pydantic>=2.9.0" \
    "kubernetes>=29.0.0" \
    "httpx>=0.27.0" \
    "openai>=1.30.0" \
    "paramiko>=3.4.0"

COPY src/ ./src/

RUN pip install --no-cache-dir -e .

RUN mkdir -p /home/app/.kube && chown -R app:app /home/app/.kube /app

USER app

EXPOSE 8123

CMD ["uvicorn", "devops_agent.main:app", "--host", "0.0.0.0", "--port", "8123"]
