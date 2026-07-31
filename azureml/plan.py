"""Decides the fan-out shape and pre-generates every OpenLineage run_id for
one simulated request, before an Azure ML pipeline job is assembled.

Azure ML pipelines are unrolled in Python in the submitting process before
submission -- there's no way to grow the DAG at runtime the way the Flask
app's in-process ThreadPoolExecutor recursion (app/job_simulator.py) does.
So the full worker/task counts and every run_id have to be known up front;
this module is the single source of truth for that, shared by both the CLI
trigger (azureml/cli.py) and the pipeline builder (azureml/submit_pipeline.py).
"""
import uuid
from dataclasses import dataclass, field
from typing import List

from app import config, openlineage_client as olc


@dataclass
class NodePlan:
    run_id: str
    name: str


@dataclass
class RequestPlan:
    request_id: str
    namespace: str

    controller: NodePlan
    controller_duration_min: float
    controller_duration_max: float
    controller_failure_rate: float
    force_fail_controller: bool
    fail_controller_on_worker_fail: bool

    workers: List[NodePlan]
    worker_duration_min: float
    worker_duration_max: float
    worker_failure_rate: float
    force_fail_worker: bool
    fail_worker_on_task_fail: bool

    # tasks[w] is the list of NodePlans dispatched by workers[w]
    tasks: List[List[NodePlan]]
    task_duration_min: float
    task_duration_max: float
    task_failure_rate: float
    force_fail_task: bool


def _bool(val, default=False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def build_request_plan(form: dict) -> RequestPlan:
    """Same defaults/fields as app.job_simulator.simulate_request()'s
    form-parsing block, plus pre-generated run_ids for every node."""
    request_id = str(uuid.uuid4())
    namespace = form.get("namespace") or config.OL_NAMESPACE
    controller_name = form.get(
        "controller_name") or "datadog.controller.request_handler"

    num_workers = int(form.get("num_workers", 3))
    num_tasks = int(form.get("num_tasks", 2))

    controller = NodePlan(run_id=olc.new_run_id(), name=controller_name)
    workers = [
        NodePlan(run_id=olc.new_run_id(), name=f"{controller_name}.worker_{w}")
        for w in range(num_workers)
    ]
    tasks = [
        [
            NodePlan(run_id=olc.new_run_id(), name=f"{workers[w].name}.task_{t}")
            for t in range(num_tasks)
        ]
        for w in range(num_workers)
    ]

    return RequestPlan(
        request_id=request_id,
        namespace=namespace,
        controller=controller,
        controller_duration_min=float(form.get("controller_duration_min", 5)),
        controller_duration_max=float(form.get("controller_duration_max", 15)),
        controller_failure_rate=float(form.get("controller_failure_rate", 0)),
        force_fail_controller=_bool(form.get("force_fail_controller", False)),
        fail_controller_on_worker_fail=_bool(
            form.get("fail_controller_on_worker_fail", True)),
        workers=workers,
        worker_duration_min=float(form.get("worker_duration_min", 10)),
        worker_duration_max=float(form.get("worker_duration_max", 20)),
        worker_failure_rate=float(form.get("worker_failure_rate", 0)),
        force_fail_worker=_bool(form.get("force_fail_worker", False)),
        fail_worker_on_task_fail=_bool(
            form.get("fail_worker_on_task_fail", True)),
        tasks=tasks,
        task_duration_min=float(form.get("task_duration_min", 60)),
        task_duration_max=float(form.get("task_duration_max", 120)),
        task_failure_rate=float(form.get("task_failure_rate", 10)),
        force_fail_task=_bool(form.get("force_fail_task", False)),
    )
