"""Builds and submits the Azure ML pipeline job for one simulated request.

Azure ML pipeline DAGs are assembled in this submitting process, in Python,
before submission -- there's no way to grow the DAG once it's running, the
way the Flask app's in-process ThreadPoolExecutor recursion does. So the
full fan-out shape and every OpenLineage run_id are decided up front by
azureml.plan.build_request_plan(), and this module loops over that plan to
instantiate one Azure ML pipeline step per controller/worker/task node.

Because a *_finalize step needs one input per child (to receive each
child's status output) and child count varies per request, components are
built dynamically here per submission rather than as fixed, static YAML
files -- see _finalize_component().
"""
import os

from azure.ai.ml import Input, MLClient, Output, command
from azure.ai.ml.dsl import pipeline
from azure.identity import DefaultAzureCredential

from azureml.plan import RequestPlan

ENVIRONMENT = os.environ.get(
    "AZUREML_SIM_ENVIRONMENT", "azureml:openlineage-job-simulator@latest")
COMPUTE = os.environ.get("AZUREML_SIM_COMPUTE", "sim-cluster")

# Experimental: datadog-init (see environment/Dockerfile) is not
# documented/supported for Azure ML job containers -- only Container
# Apps/App Service. It's wired in here as a command-string prefix rather
# than a Dockerfile ENTRYPOINT, since Azure ML likely execs a step's
# `command:` directly. Treat its output as unverified until smoke-tested.
# Set AZUREML_SIM_USE_SERVERLESS_INIT=false to fall back to plain library
# calls with no in-container agent at all.
_USE_SERVERLESS_INIT = os.environ.get(
    "AZUREML_SIM_USE_SERVERLESS_INIT", "true").strip().lower() in ("1", "true", "yes", "on")

# Same knobs as .env.example, minus DD_API_KEY (resolved separately, see
# _resolve_dd_api_key -- never checked into a component spec or Dockerfile).
_BASE_ENV_VARS = {
    "DD_SITE": os.environ.get("DD_SITE", "datadoghq.com"),
    "DD_SERVICE": os.environ.get("DD_SERVICE", "openlineage-worker-demo"),
    "DD_ENV": os.environ.get("DD_ENV", "azureml-demo"),
    "OL_TRANSPORT": os.environ.get("OL_TRANSPORT", "datadog"),
    "OL_NAMESPACE": os.environ.get("OL_NAMESPACE", "demo.datadog.azureml"),
    "OL_PRODUCER": os.environ.get(
        "OL_PRODUCER", "https://github.com/datadog/openlineage-do-jobs"),
    # datadog-init captures each step's stdout and forwards it itself, so
    # LOG_SHIP_MODE=agent (stdout only) avoids double-shipping through our
    # own HTTP handler. Falls back to direct HTTP shipping if serverless-init
    # is disabled, since then nothing else is tailing stdout.
    "LOG_SHIP_MODE": "agent" if _USE_SERVERLESS_INIT else "http",
    "LOG_LEVEL": os.environ.get("LOG_LEVEL", "INFO"),
}
if _USE_SERVERLESS_INIT:
    # Per https://docs.datadoghq.com/serverless/azure_container_apps/in_container/python/ --
    # datadog-init does NOT forward stdout/stderr to Datadog Logs unless this
    # is explicitly set (it defaults to false). Without it, LOG_SHIP_MODE=agent
    # above ships nothing at all.
    _BASE_ENV_VARS["DD_LOGS_ENABLED"] = "true"
    _BASE_ENV_VARS["DD_LOGS_INJECTION"] = os.environ.get("DD_LOGS_INJECTION", "true")


def _wrap(cmd: str) -> str:
    """Prefixes a step's command with the datadog-init + ddtrace-run wrapper,
    when enabled -- matching Datadog's documented in-container pattern
    (https://docs.datadoghq.com/serverless/azure_container_apps/in_container/python/):
    `datadog-init` as the top-level process, `ddtrace-run` as its argument.
    ddtrace-run is only meaningful alongside datadog-init here, since
    datadog-init is what runs the local trace agent ddtrace sends spans to --
    there's no host Datadog Agent on AmlCompute for it to reach otherwise.
    Each step opens its own span (see azureml/steps/common.py's
    traced_step()); this is the pipe those spans travel through to reach
    datadog-init's local trace agent."""
    if not _USE_SERVERLESS_INIT:
        return cmd
    return f"/app/datadog-init ddtrace-run {cmd}"


def _resolve_dd_api_key() -> dict:
    """Resolve DD_API_KEY, preferring the workspace's Key Vault. Falls back
    to the DD_API_KEY environment variable (e.g. from azureml/.env, gitignored)
    if AZUREML_SIM_KEYVAULT_URL isn't set -- convenient for local iteration,
    but Key Vault is recommended so the key never has to live in a plaintext
    file you could accidentally commit."""
    vault_url = os.environ.get("AZUREML_SIM_KEYVAULT_URL", "").strip()
    if vault_url:
        from azure.keyvault.secrets import SecretClient

        secret_name = os.environ.get(
            "AZUREML_SIM_DD_API_KEY_SECRET", "DD-API-KEY")
        client = SecretClient(vault_url=vault_url,
                               credential=DefaultAzureCredential())
        return {"DD_API_KEY": client.get_secret(secret_name).value}

    api_key = os.environ.get("DD_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "Set AZUREML_SIM_KEYVAULT_URL (recommended, see azureml/README.md) "
            "or DD_API_KEY (e.g. in azureml/.env) before submitting a pipeline."
        )
    return {"DD_API_KEY": api_key}


def _start_component(role_label: str):
    return command(
        name=f"sim_{role_label}_start",
        display_name=f"{role_label} start",
        environment=ENVIRONMENT,
        command=_wrap(
            "python -m azureml.steps.run_node_start "
            "--namespace ${{inputs.namespace}} --name ${{inputs.name}} "
            "--run-id ${{inputs.run_id}} --job-type ${{inputs.job_type}} "
            "--duration-min ${{inputs.duration_min}} --duration-max ${{inputs.duration_max}} "
            "--root-run-id ${{inputs.root_run_id}} --root-name ${{inputs.root_name}} "
            "$[[--parent-run-id ${{inputs.parent_run_id}}]] "
            "$[[--parent-name ${{inputs.parent_name}}]]"
        ),
        inputs={
            "namespace": Input(type="string"),
            "name": Input(type="string"),
            "run_id": Input(type="string"),
            "job_type": Input(type="string"),
            "duration_min": Input(type="number"),
            "duration_max": Input(type="number"),
            "root_run_id": Input(type="string"),
            "root_name": Input(type="string"),
            "parent_run_id": Input(type="string", optional=True),
            "parent_name": Input(type="string", optional=True),
        },
    )


def _task_component():
    return command(
        name="sim_task",
        display_name="task",
        environment=ENVIRONMENT,
        command=_wrap(
            "python -m azureml.steps.task_entrypoint "
            "--namespace ${{inputs.namespace}} --name ${{inputs.name}} "
            "--run-id ${{inputs.run_id}} "
            "--duration-min ${{inputs.duration_min}} --duration-max ${{inputs.duration_max}} "
            "--failure-rate ${{inputs.failure_rate}} "
            "--parent-run-id ${{inputs.parent_run_id}} --parent-name ${{inputs.parent_name}} "
            "--root-run-id ${{inputs.root_run_id}} --root-name ${{inputs.root_name}} "
            "$[[--force-fail]] "
            "--status-out ${{outputs.status_out}}"
        ),
        inputs={
            "namespace": Input(type="string"),
            "name": Input(type="string"),
            "run_id": Input(type="string"),
            "duration_min": Input(type="number"),
            "duration_max": Input(type="number"),
            "failure_rate": Input(type="number"),
            "parent_run_id": Input(type="string"),
            "parent_name": Input(type="string"),
            "root_run_id": Input(type="string"),
            "root_name": Input(type="string"),
            "force_fail": Input(type="boolean", optional=True, default=False),
        },
        outputs={"status_out": Output(type="uri_file")},
    )


def _finalize_component(role_label: str, child_role_label: str, num_children: int):
    """role_label describes this node ("controller"/"worker"); child_role_label
    describes what kind of children it cascades failure from ("worker"/"task"),
    matching children_spec["role"] in the original app.job_simulator code."""
    child_inputs = {f"child_{i}": Input(type="uri_file")
                     for i in range(num_children)}
    child_flags = " ".join(
        f"--child-status ${{{{inputs.child_{i}}}}}" for i in range(num_children))
    return command(
        name=f"sim_{role_label}_finalize_{num_children}",
        display_name=f"{role_label} finalize ({num_children} children)",
        environment=ENVIRONMENT,
        command=_wrap((
            "python -m azureml.steps.run_node_finalize "
            "--namespace ${{inputs.namespace}} --name ${{inputs.name}} "
            "--run-id ${{inputs.run_id}} --job-type ${{inputs.job_type}} "
            "--failure-rate ${{inputs.failure_rate}} "
            f"--role-label {child_role_label} "
            "--root-run-id ${{inputs.root_run_id}} --root-name ${{inputs.root_name}} "
            "$[[--parent-run-id ${{inputs.parent_run_id}}]] "
            "$[[--parent-name ${{inputs.parent_name}}]] "
            "$[[--force-fail]] $[[--fail-on-child-fail]] "
            f"{child_flags} "
            "--status-out ${{outputs.status_out}}"
        ).strip()),
        inputs={
            "namespace": Input(type="string"),
            "name": Input(type="string"),
            "run_id": Input(type="string"),
            "job_type": Input(type="string"),
            "failure_rate": Input(type="number"),
            "root_run_id": Input(type="string"),
            "root_name": Input(type="string"),
            "parent_run_id": Input(type="string", optional=True),
            "parent_name": Input(type="string", optional=True),
            "force_fail": Input(type="boolean", optional=True, default=False),
            "fail_on_child_fail": Input(type="boolean", optional=True, default=False),
            **child_inputs,
        },
        outputs={"status_out": Output(type="uri_file")},
    )


def build_pipeline(plan: RequestPlan, dd_api_key_env: dict):
    controller_start = _start_component("controller")
    worker_start = _start_component("worker")
    task_component = _task_component()
    num_tasks_per_worker = len(plan.tasks[0]) if plan.tasks else 0
    worker_finalize = _finalize_component(
        "worker", "task", num_tasks_per_worker)
    controller_finalize = _finalize_component(
        "controller", "worker", len(plan.workers))

    env_vars = {**_BASE_ENV_VARS, **dd_api_key_env}

    def _with_env(step):
        step.environment_variables = env_vars
        return step

    @pipeline(name=f"ol-sim-{plan.request_id}",
              description="OpenLineage job simulator run", compute=COMPUTE)
    def sim_pipeline():
        _with_env(controller_start(
            namespace=plan.namespace, name=plan.controller.name, run_id=plan.controller.run_id,
            job_type="JOB", duration_min=plan.controller_duration_min,
            duration_max=plan.controller_duration_max,
            root_run_id=plan.controller.run_id, root_name=plan.controller.name,
        ))

        worker_status_outputs = []
        for w, worker in enumerate(plan.workers):
            _with_env(worker_start(
                namespace=plan.namespace, name=worker.name, run_id=worker.run_id,
                job_type="TASK", duration_min=plan.worker_duration_min,
                duration_max=plan.worker_duration_max,
                root_run_id=plan.controller.run_id, root_name=plan.controller.name,
                parent_run_id=plan.controller.run_id, parent_name=plan.controller.name,
            ))

            task_status_outputs = []
            for task in plan.tasks[w]:
                task_step = _with_env(task_component(
                    namespace=plan.namespace, name=task.name, run_id=task.run_id,
                    duration_min=plan.task_duration_min, duration_max=plan.task_duration_max,
                    failure_rate=plan.task_failure_rate, force_fail=plan.force_fail_task,
                    parent_run_id=worker.run_id, parent_name=worker.name,
                    root_run_id=plan.controller.run_id, root_name=plan.controller.name,
                ))
                task_status_outputs.append(task_step.outputs.status_out)

            w_finalize_inputs = {
                f"child_{i}": out for i, out in enumerate(task_status_outputs)}
            w_finalize = _with_env(worker_finalize(
                namespace=plan.namespace, name=worker.name, run_id=worker.run_id,
                job_type="TASK", failure_rate=plan.worker_failure_rate,
                force_fail=plan.force_fail_worker,
                fail_on_child_fail=plan.fail_worker_on_task_fail,
                root_run_id=plan.controller.run_id, root_name=plan.controller.name,
                parent_run_id=plan.controller.run_id, parent_name=plan.controller.name,
                **w_finalize_inputs,
            ))
            worker_status_outputs.append(w_finalize.outputs.status_out)

        c_finalize_inputs = {
            f"child_{i}": out for i, out in enumerate(worker_status_outputs)}
        _with_env(controller_finalize(
            namespace=plan.namespace, name=plan.controller.name, run_id=plan.controller.run_id,
            job_type="JOB", failure_rate=plan.controller_failure_rate,
            force_fail=plan.force_fail_controller,
            fail_on_child_fail=plan.fail_controller_on_worker_fail,
            root_run_id=plan.controller.run_id, root_name=plan.controller.name,
            **c_finalize_inputs,
        ))

    return sim_pipeline()


def get_ml_client() -> MLClient:
    return MLClient(
        DefaultAzureCredential(),
        subscription_id=os.environ["AZUREML_SIM_SUBSCRIPTION_ID"],
        resource_group_name=os.environ["AZUREML_SIM_RESOURCE_GROUP"],
        workspace_name=os.environ["AZUREML_SIM_WORKSPACE"],
    )


def submit(plan: RequestPlan) -> str:
    ml_client = get_ml_client()
    dd_api_key_env = _resolve_dd_api_key()
    pipeline_job = build_pipeline(plan, dd_api_key_env)
    submitted = ml_client.jobs.create_or_update(pipeline_job)
    return submitted.name
