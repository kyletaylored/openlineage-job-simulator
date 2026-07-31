# Custom Azure ML environment for the OpenLineage job simulator's pipeline
# steps. Build context must be the repo root (so app/ and azureml/ are both
# available) -- see azureml/README.md for the exact `az ml environment
# create` invocation.
FROM mcr.microsoft.com/azureml/openmpi4.1.0-ubuntu20.04

WORKDIR /app

COPY requirements.txt azureml/requirements-azureml.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-azureml.txt

COPY app ./app
COPY azureml ./azureml

ENV PYTHONPATH=/app
