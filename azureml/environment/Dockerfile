# Custom Azure ML environment for the OpenLineage job simulator's pipeline
# steps. Build context must be the repo root (so app/ and azureml/ are both
# available) -- see azureml/README.md for the exact `az ml environment
# create` invocation.
FROM datadog/serverless-init:1 AS dd-init

# ubuntu24.04, not ubuntu20.04: datadog-init needs glibc >= 2.32/2.34, which
# ubuntu20.04's glibc 2.31 doesn't satisfy (confirmed by a failed smoke-test
# run -- see "Experimental: Datadog serverless-init" in README.md).
FROM mcr.microsoft.com/azureml/openmpi5.0-ubuntu24.04

WORKDIR /app

COPY requirements.txt azureml/requirements-azureml.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-azureml.txt

COPY app ./app
COPY azureml ./azureml

# Experimental: datadog-init isn't documented/supported for Azure ML job
# containers (only Container Apps/App Service). It's wired in as a per-step
# command-string prefix (see submit_pipeline.py), not a Dockerfile
# ENTRYPOINT, because Azure ML likely execs a step's `command:` directly and
# may not honor an image ENTRYPOINT. Treat its output as unverified until
# smoke-tested. See "Experimental: Datadog serverless-init" in README.md.
COPY --from=dd-init /datadog-init /app/datadog-init
RUN chmod +x /app/datadog-init

ENV PYTHONPATH=/app
# Recommended alongside datadog-init (see
# https://docs.datadoghq.com/serverless/azure_container_apps/in_container/python/):
# unbuffered stdout so logs reach datadog-init immediately, and DD_SOURCE for
# Python-specific log pipeline parsing.
ENV PYTHONUNBUFFERED=1
ENV DD_SOURCE=python
