"""Rebuilds and re-registers the Azure ML custom environment (Docker image)
from azureml/environment/Dockerfile, then updates AZUREML_SIM_ENVIRONMENT in
azureml/.env (and azureml/.env.example) to point at the newly-created version.

Run this whenever app/ or azureml/ code that executes INSIDE a job
container changes, or the Dockerfile itself changes. Driver-only changes
(submit_pipeline.py/plan.py/cli.py's own logic, which runs locally rather
than inside a job container) don't need this -- they take effect on the
next `python -m azureml.cli` call directly.

Environments are immutable and versioned: this always creates a new
version (never overwrites an existing one), which is why the resulting
AZUREML_SIM_ENVIRONMENT value changes on every run.
"""
import os
import re
from pathlib import Path

from azure.ai.ml import MLClient
from azure.ai.ml.entities import BuildContext, Environment
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent
_ENV_NAME = "openlineage-job-simulator"

load_dotenv(_HERE / ".env")


def _update_env_file(path: Path, version: str) -> None:
    if not path.exists():
        return
    text = path.read_text()
    new_text, count = re.subn(
        r"^AZUREML_SIM_ENVIRONMENT=.*$",
        f"AZUREML_SIM_ENVIRONMENT=azureml:{_ENV_NAME}:{version}",
        text,
        flags=re.MULTILINE,
    )
    if count:
        path.write_text(new_text)
        print(f"Updated {path} -> azureml:{_ENV_NAME}:{version}")


def main() -> None:
    ml_client = MLClient(
        DefaultAzureCredential(),
        subscription_id=os.environ["AZUREML_SIM_SUBSCRIPTION_ID"],
        resource_group_name=os.environ["AZUREML_SIM_RESOURCE_GROUP"],
        workspace_name=os.environ["AZUREML_SIM_WORKSPACE"],
    )

    env = Environment(
        name=_ENV_NAME,
        description="OpenLineage job simulator pipeline steps (app/ + azureml/)",
        build=BuildContext(
            path=str(_REPO_ROOT), dockerfile_path="azureml/environment/Dockerfile"
        ),
    )

    print(f"Registering environment {_ENV_NAME} (triggers an ACR build, can take several minutes)...")
    result = ml_client.environments.create_or_update(env)
    print(f"Environment registered: {result.name}:{result.version}")

    _update_env_file(_HERE / ".env", result.version)
    _update_env_file(_HERE / ".env.example", result.version)


if __name__ == "__main__":
    main()
