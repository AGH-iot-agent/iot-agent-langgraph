FROM python:3.12-slim AS base

RUN groupadd --gid 1000 app && \
    useradd --uid 1000 --gid app --shell /bin/sh --create-home app

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      gcc \
      curl \
      ca-certificates \
      gnupg \
      smbclient \
      gh && \
    rm -rf /var/lib/apt/lists/*

RUN KUBECTL_VERSION="$(curl -fsSL https://dl.k8s.io/release/stable.txt)" && \
    curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" -o /usr/local/bin/kubectl && \
    chmod +x /usr/local/bin/kubectl && \
    kubectl version --client

RUN HELM_VERSION="v3.16.4" && \
    curl -fsSL "https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz" -o /tmp/helm.tgz && \
    tar -xzf /tmp/helm.tgz -C /tmp && \
    mv /tmp/linux-amd64/helm /usr/local/bin/helm && \
    chmod +x /usr/local/bin/helm && \
    rm -rf /tmp/helm.tgz /tmp/linux-amd64 && \
    helm version --short

COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[all]" 2>/dev/null || pip install --no-cache-dir \
    "fastapi>=0.115.0" \
    "uvicorn>=0.30.0" \
    "langgraph>=0.2.0" \
    "pydantic>=2.9.0" \
    "kubernetes>=29.0.0" \
    "httpx>=0.27.0" \
    "openai>=1.30.0" \
    "paramiko>=3.4.0" \
    "prometheus_client>=0.20.0"

COPY src/ ./src/

RUN pip install --no-cache-dir -e .
RUN pip install --no-cache-dir -e ".[guardrails]"
RUN python -m spacy download en_core_web_sm

RUN mkdir -p /home/app/.kube && chown -R app:app /home/app/.kube /app

USER app

EXPOSE 8123

CMD ["uvicorn", "devops_agent.main:app", "--host", "0.0.0.0", "--port", "8123"]
