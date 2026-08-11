"""Standalone trigger for the Azure ML path.

Flask's POST /api/simulate stays the trigger for the local/non-Azure demo --
this is additive, not a replacement. Usage:

    python -m azureml.cli --num-workers 2 --num-tasks 2

Requires AZUREML_SIM_SUBSCRIPTION_ID, AZUREML_SIM_RESOURCE_GROUP,
AZUREML_SIM_WORKSPACE, and AZUREML_SIM_KEYVAULT_URL to be set (see
azureml/README.md), plus being logged in via `az login` (DefaultAzureCredential).
"""
import argparse
from pathlib import Path

from dotenv import load_dotenv

# Loaded explicitly (rather than relying on app.config's load_dotenv() import
# side-effect) so this works regardless of import order or cwd.
load_dotenv(Path(__file__).resolve().parent / ".env")

from azureml.plan import build_request_plan
from azureml.submit_pipeline import submit


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--controller-name")
    p.add_argument("--namespace")
    p.add_argument("--num-workers", type=int, default=3)
    p.add_argument("--num-tasks", type=int, default=2)
    p.add_argument("--controller-duration-min", type=float, default=5)
    p.add_argument("--controller-duration-max", type=float, default=15)
    p.add_argument("--controller-failure-rate", type=float, default=0)
    p.add_argument("--worker-duration-min", type=float, default=10)
    p.add_argument("--worker-duration-max", type=float, default=20)
    p.add_argument("--worker-failure-rate", type=float, default=0)
    p.add_argument("--task-duration-min", type=float, default=60)
    p.add_argument("--task-duration-max", type=float, default=120)
    p.add_argument("--task-failure-rate", type=float, default=0)
    p.add_argument("--force-fail-controller", action="store_true")
    p.add_argument("--force-fail-worker", action="store_true")
    p.add_argument("--force-fail-task", action="store_true")
    p.add_argument("--no-fail-controller-on-worker-fail",
                    dest="fail_controller_on_worker_fail", action="store_false")
    p.add_argument("--no-fail-worker-on-task-fail",
                    dest="fail_worker_on_task_fail", action="store_false")
    p.set_defaults(fail_controller_on_worker_fail=True,
                    fail_worker_on_task_fail=True)
    return p.parse_args()


def main():
    args = parse_args()
    form = {k: v for k, v in vars(args).items() if v is not None}

    plan = build_request_plan(form)
    job_name = submit(plan)
    print(f"Submitted Azure ML pipeline job: {job_name}")
    print(f"Controller OpenLineage run_id: {plan.controller.run_id}")
    print(f"Request id: {plan.request_id}")


if __name__ == "__main__":
    main()
