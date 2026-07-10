"""Wrapper around the OpenLineage Python client: transport setup + event/facet construction."""
import logging
from datetime import datetime, timezone

from openlineage.client import OpenLineageClient
from openlineage.client.event_v2 import (
    Dataset,
    Job,
    Run,
    RunEvent,
    RunState,
)
from openlineage.client.facet_v2 import (
    error_message_run,
    job_type_job,
    parent_run,
    tags_job,
)
from openlineage.client.uuid import generate_new_uuid

from app import config

log = logging.getLogger("openlineage_do_jobs")


def build_client() -> OpenLineageClient:
    """Build the OpenLineage client using the configured transport."""
    config.require_api_key()

    if config.OL_TRANSPORT == "datadog":
        try:
            from openlineage.client.transport.datadog import DatadogConfig, DatadogTransport
        except ImportError as exc:
            raise RuntimeError(
                "DatadogTransport requires openlineage-python >= 1.37.0. "
                "Upgrade with `pip install -U openlineage-python`."
            ) from exc
        transport = DatadogTransport(
            DatadogConfig(apiKey=config.DD_API_KEY, site=config.DD_SITE)
        )
        return OpenLineageClient(transport=transport)

    if config.OL_TRANSPORT == "http":
        from openlineage.client import OpenLineageClientOptions
        from openlineage.client.transport.datadog import SITE_MAPPING

        intake_url = SITE_MAPPING.get(
            config.DD_SITE, f"https://data-obs-intake.{config.DD_SITE}")
        return OpenLineageClient(
            url=intake_url,
            options=OpenLineageClientOptions(api_key=config.DD_API_KEY),
        )

    raise RuntimeError(
        f"Unknown OL_TRANSPORT: {config.OL_TRANSPORT!r} (expected 'datadog' or 'http')")


def new_run_id() -> str:
    return str(generate_new_uuid())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def job_facets(job_type: str, ol_service: str) -> dict:
    """Required jobType + tags facets for every job."""
    return {
        "jobType": job_type_job.JobTypeJobFacet(
            processingType="BATCH",
            integration="datadog-demo",
            jobType=job_type,
        ),
        "tags": tags_job.TagsJobFacet(
            tags=[
                tags_job.TagsJobFacetFields(
                    key="_dd.ol_service", value=ol_service, source="DATADOG_DEMO"
                )
            ]
        ),
    }


def parent_facet(parent_namespace: str, parent_name: str, parent_run_id: str,
                 root_namespace: str, root_name: str, root_run_id: str) -> dict:
    """parent run facet linking a worker run back to its dispatching controller + root."""
    return {
        "parent": parent_run.ParentRunFacet(
            run=parent_run.Run(runId=parent_run_id),
            job=parent_run.Job(namespace=parent_namespace, name=parent_name),
            root=parent_run.Root(
                run=parent_run.RootRun(runId=root_run_id),
                job=parent_run.RootJob(
                    namespace=root_namespace, name=root_name),
            ),
        )
    }


def error_facet(message: str, stack_trace: str) -> dict:
    return {
        "errorMessage": error_message_run.ErrorMessageRunFacet(
            message=message,
            programmingLanguage="PYTHON",
            stackTrace=stack_trace,
        )
    }


def make_dataset(namespace: str, name: str) -> Dataset:
    return Dataset(namespace=namespace, name=name)


DEFAULT_INPUT = ("postgres://demo-db.example.com:5432", "orders.public.orders")
DEFAULT_OUTPUT = ("snowflake://demo-org-demo-account",
                  "ANALYTICS.PUBLIC.ORDERS")


def emit_start(client: OpenLineageClient, *, namespace: str, name: str, run_id: str,
               job_type: str, ol_service: str, run_facets: dict = None,
               inputs=None, outputs=None):
    run_facets = run_facets or {}
    job = Job(namespace=namespace, name=name,
              facets=job_facets(job_type, ol_service))
    run = Run(runId=run_id, facets=run_facets)
    inputs = inputs or [make_dataset(*DEFAULT_INPUT)]
    outputs = outputs or [make_dataset(*DEFAULT_OUTPUT)]
    event = RunEvent(
        eventType=RunState.START,
        eventTime=_now(),
        run=run,
        job=job,
        producer=config.OL_PRODUCER,
        inputs=inputs,
        outputs=outputs,
    )
    client.emit(event)
    log.info("emitted OpenLineage START", extra={
             "run_id": run_id, "job_name": name})


def emit_terminal(client: OpenLineageClient, *, namespace: str, name: str, run_id: str,
                  job_type: str, ol_service: str, state: str, run_facets: dict = None):
    run_facets = run_facets or {}
    job = Job(namespace=namespace, name=name,
              facets=job_facets(job_type, ol_service))
    run = Run(runId=run_id, facets=run_facets)
    event_state = {
        "COMPLETE": RunState.COMPLETE,
        "FAIL": RunState.FAIL,
        "ABORT": RunState.ABORT,
    }[state]
    event = RunEvent(
        eventType=event_state,
        eventTime=_now(),
        run=run,
        job=job,
        producer=config.OL_PRODUCER,
    )
    client.emit(event)
    log.info("emitted OpenLineage %s", state, extra={
             "run_id": run_id, "job_name": name})
