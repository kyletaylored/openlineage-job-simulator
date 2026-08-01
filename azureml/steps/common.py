"""Shared helpers for Azure ML step entrypoints: OpenLineage emission,
status-file I/O used to aggregate child pass/fail across separate step
containers, and the failure-decision math.

This intentionally does not import app.job_simulator: that module's module
state (in-memory live-run tracking, SQLite persistence) is specific to the
Flask app's single-process threading model and doesn't apply once each node
is its own container (see azureml/README.md "What's different"). The
failure-decision math is duplicated here as a small pure function rather
than imported, to keep this package's only dependency on app/ being the
OpenLineage client + logging setup.

No ddtrace spans are opened here -- APM distributed tracing across separate
pipeline step containers is deferred to a follow-up phase.
"""
import json
import logging
import os
import random
import traceback

from app import config, logging_setup
from app import openlineage_client as olc

log = logging.getLogger("azureml.job_simulator")

_ol_client = None


def configure():
    logging_setup.configure_logging()


def get_client():
    global _ol_client
    if _ol_client is None:
        _ol_client = olc.build_client()
    return _ol_client


def aml_job_name() -> str:
    return os.environ.get("AZUREML_RUN_ID")


def ol_service_name(job_type: str) -> str:
    suffix = "controller" if job_type == "JOB" else "worker"
    return f"{config.DD_SERVICE}-{suffix}"


def build_run_facets(*, parent_run_id, parent_name, root_run_id, root_name, namespace):
    if not parent_run_id:
        return {}
    return olc.parent_facet(
        parent_namespace=namespace, parent_name=parent_name, parent_run_id=parent_run_id,
        root_namespace=namespace, root_name=root_name, root_run_id=root_run_id,
    )


def emit_start(*, namespace, name, run_id, job_type, ol_service, run_facets):
    olc.emit_start(
        get_client(), namespace=namespace, name=name, run_id=run_id, job_type=job_type,
        ol_service=ol_service, run_facets=run_facets, aml_job_name=aml_job_name(),
    )
    log.info("job started", extra={
        "run_id": run_id, "job_name": name, "job_namespace": namespace, "job_type": job_type,
    })


def decide_failure(*, force_fail, failure_rate, any_child_failed, fail_on_child_fail) -> bool:
    """Ported unchanged from app.job_simulator._run_node's will_fail check."""
    return bool(
        force_fail
        or (random.uniform(0, 100) < failure_rate)
        or (any_child_failed and fail_on_child_fail)
    )


def emit_terminal(*, namespace, name, run_id, job_type, ol_service, run_facets,
                   will_fail, any_child_failed, fail_on_child_fail, role_label):
    """Returns (status, error_message)."""
    client = get_client()
    if will_fail:
        if any_child_failed and fail_on_child_fail:
            message = f"'{name}' failed because one or more {role_label}s failed"
        else:
            message = f"Simulated failure while processing job '{name}'"
        try:
            raise RuntimeError(message)
        except RuntimeError:
            stack_trace = traceback.format_exc()
            log.error("job failed", exc_info=True, extra={
                "run_id": run_id, "job_name": name, "job_namespace": namespace, "job_type": job_type,
            })
        olc.emit_terminal(
            client, namespace=namespace, name=name, run_id=run_id, job_type=job_type,
            ol_service=ol_service, state="FAIL",
            run_facets={**run_facets, **olc.error_facet(message, stack_trace)},
            aml_job_name=aml_job_name(),
        )
        return "FAIL", message

    olc.emit_terminal(
        client, namespace=namespace, name=name, run_id=run_id, job_type=job_type,
        ol_service=ol_service, state="COMPLETE", run_facets=run_facets,
        aml_job_name=aml_job_name(),
    )
    log.info("job completed", extra={
        "run_id": run_id, "job_name": name, "job_namespace": namespace, "job_type": job_type,
    })
    return "COMPLETE", None


def write_status(path: str, status: str, error_message: str = None):
    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    with open(path, "w") as f:
        json.dump({"status": status, "error_message": error_message}, f)


def read_status(path: str) -> dict:
    with open(path) as f:
        return json.load(f)
