"""Core simulation: controller receives a request, dispatches worker runs,
and each worker can itself dispatch its own sub-tasks (3 levels deep).

Mirrors customers actual pattern: request-driven fan-out, not a
DAG/scheduler. Context (parent job identity) is passed explicitly between
every level -- controller to worker, worker to task -- the way it would be
passed in a real container/queue payload; there is no implicit propagation.
"""
import random
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor

from ddtrace import tracer

from app import config, models, openlineage_client as olc
from app.logging_setup import get_logger

log = get_logger("job_simulator")

_ol_client = None
_ol_client_lock = threading.Lock()

# In-memory live status, keyed by request_id -> list of run dicts (mirrors DB, for fast polling)
_live_requests = {}
_live_lock = threading.Lock()

# Surfaced in the UI status banner: last OpenLineage emit outcome.
_last_emit_status = {"ok": True, "error": None, "at": None}
_last_emit_lock = threading.Lock()

# Continuous ("cron") mode: repeatedly calls simulate_request() on an
# interval until stopped or a time budget runs out, so the demo can show a
# steady stream of jobs in Datadog instead of one at a time.
_scheduler_lock = threading.Lock()
_scheduler_state = {
    "running": False,
    "stop_event": None,
    "started_at": None,
    "dispatch_count": 0,
    "interval_seconds": None,
    "deadline": None,
}


def get_client():
    global _ol_client
    with _ol_client_lock:
        if _ol_client is None:
            _ol_client = olc.build_client()
        return _ol_client


def get_last_emit_status():
    with _last_emit_lock:
        return dict(_last_emit_status)


def _record_emit_result(ok: bool, error: str = None):
    with _last_emit_lock:
        _last_emit_status["ok"] = ok
        _last_emit_status["error"] = error
        _last_emit_status["at"] = time.time()


def _safe_emit(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
        _record_emit_result(True)
    except Exception as exc:
        log.error("OpenLineage emit failed: %s", exc, exc_info=True)
        _record_emit_result(False, str(exc))


def _update_live(request_id, run_id, **fields):
    with _live_lock:
        runs = _live_requests.setdefault(request_id, {})
        run = runs.setdefault(run_id, {"run_id": run_id})
        run.update(fields)


def get_live_request(request_id):
    with _live_lock:
        runs = _live_requests.get(request_id, {})
        return list(runs.values())


# How long a finished run stays visible in the "live" view before it drops
# off (so a just-completed run doesn't vanish mid-blink), and how long a
# whole request's runs are kept in memory at all before being pruned --
# full history is persisted in SQLite regardless, this is only the fast
# in-memory view for polling, and Continuous Mode can otherwise run
# indefinitely and grow it without bound.
_LIVE_RECENTLY_DONE_SECONDS = 15
_LIVE_RETENTION_SECONDS = 300


def get_all_live_runs():
    """All runs currently in flight (or that finished in the last
    `_LIVE_RECENTLY_DONE_SECONDS`) across every request -- not just one --
    so the Live Runs view reflects Continuous Mode's overlapping requests
    too, not only the single request_id a manual "Simulate Request" click
    returns.
    """
    now = time.time()
    cutoff = now - _LIVE_RETENTION_SECONDS
    with _live_lock:
        stale_request_ids = [
            rid for rid, runs in _live_requests.items()
            if runs and all(
                r.get("ended_at") is not None and r["ended_at"] < cutoff
                for r in runs.values()
            )
        ]
        for rid in stale_request_ids:
            del _live_requests[rid]

        flattened = []
        for request_id, runs in _live_requests.items():
            for run in runs.values():
                r = dict(run)
                r.setdefault("request_id", request_id)
                flattened.append(r)

    active = [
        r for r in flattened
        if r.get("status") not in ("COMPLETE", "FAIL")
        or now - (r.get("ended_at") or 0) < _LIVE_RECENTLY_DONE_SECONDS
    ]
    active.sort(key=lambda r: r.get("started_at") or 0)
    return active


def _ol_service_name(job_type: str) -> str:
    suffix = "controller" if job_type == "JOB" else "worker"
    return f"{config.DD_SERVICE}-{suffix}"


def _raise_simulated_error(job_name):
    def _inner():
        raise RuntimeError(
            f"Simulated failure while processing job '{job_name}'")

    _inner()


def _run_node(*, name, namespace, job_type, duration_min, duration_max, failure_rate,
              force_fail, request_id, run_id, parent=None, root=None,
              parent_trace_context=None, children_spec=None):
    """Execute one simulated job run end-to-end: OpenLineage START -> [own
    overhead] -> [dispatch + wait for children, if any] -> OpenLineage
    terminal event. Recurses for children_spec, so the same function runs
    the controller, its workers, and each worker's own sub-tasks.

    parent: dict(namespace, name, run_id) of the dispatching job, or None if this is the root.
    root: dict(namespace, name, run_id) of the top-level job in the chain; stays pinned to the
        root even when this call is itself several levels deep.
    parent_trace_context: the dispatching job's ddtrace Context, explicitly threaded through
        since children run in a different thread -- ddtrace does not auto-propagate active
        spans across thread boundaries.
    children_spec: optional dict describing what this job fans out to (see simulate_request).
    """
    client = get_client()
    ol_service = _ol_service_name(job_type)
    this_identity = {"namespace": namespace, "name": name, "run_id": run_id}
    root = root or this_identity

    models.create_run(
        run_id=run_id,
        parent_run_id=parent["run_id"] if parent else None,
        root_run_id=root["run_id"],
        namespace=namespace,
        name=name,
        job_type=job_type,
        ol_service=ol_service,
        request_id=request_id,
    )
    _update_live(
        request_id, run_id,
        name=name, namespace=namespace, job_type=job_type,
        status="pending", parent_run_id=parent["run_id"] if parent else None,
    )

    run_facets = {}
    if parent:
        run_facets.update(
            olc.parent_facet(
                parent_namespace=parent["namespace"],
                parent_name=parent["name"],
                parent_run_id=parent["run_id"],
                root_namespace=root["namespace"],
                root_name=root["name"],
                root_run_id=root["run_id"],
            )
        )

    started_at = time.time()
    span = tracer.start_span(
        "job.run", child_of=parent_trace_context, service=ol_service,
        resource=name, activate=True,
    )
    try:
        span.set_tag("run_id", run_id)
        span.set_tag("job.namespace", namespace)
        span.set_tag("job.name", name)
        span.set_tag("job.type", job_type)

        models.mark_started(run_id, started_at=_iso(started_at))
        _update_live(request_id, run_id, status="running",
                     started_at=started_at)

        _safe_emit(
            olc.emit_start,
            client,
            namespace=namespace, name=name, run_id=run_id,
            job_type=job_type, ol_service=ol_service, run_facets=run_facets,
        )
        log.info(
            "job started", extra={"run_id": run_id, "job_name": name,
                                  "job_namespace": namespace, "job_type": job_type},
        )

        # Own overhead/work time. If this job also has children, this is
        # additive on top of the wait below -- this job can never finish
        # before its slowest child since it blocks on every child's result.
        time.sleep(random.uniform(duration_min, duration_max))

        # Fan out children concurrently, if configured -- explicit parent-context
        # payload per child, exactly as a real dispatcher would pass parent job id
        # to sub-containers. The ddtrace Context is captured and threaded through
        # the same way, since children run on a different thread than the one that
        # started this span.
        child_results = []
        if children_spec and children_spec.get("count", 0) > 0:
            this_trace_context = tracer.current_trace_context()
            role = children_spec["role"]
            with ThreadPoolExecutor(max_workers=children_spec["count"]) as pool:
                futures = [
                    pool.submit(
                        _run_node,
                        name=f"{name}.{role}_{i}", namespace=namespace,
                        job_type=children_spec["job_type"],
                        duration_min=children_spec["duration_min"],
                        duration_max=children_spec["duration_max"],
                        failure_rate=children_spec["failure_rate"],
                        force_fail=children_spec["force_fail"],
                        request_id=request_id, run_id=olc.new_run_id(),
                        parent=this_identity, root=root,
                        parent_trace_context=this_trace_context,
                        children_spec=children_spec.get("children_spec"),
                    )
                    for i in range(children_spec["count"])
                ]
                child_results = [f.result() for f in futures]

        any_child_failed = any(r["status"] == "FAIL" for r in child_results)
        will_fail = force_fail or (random.uniform(0, 100) < failure_rate) or (
            any_child_failed and children_spec and children_spec.get(
                "fail_parent_on_child_fail")
        )

        ended_at = time.time()
        if will_fail:
            try:
                _raise_simulated_error(name)
            except RuntimeError:
                stack_trace = traceback.format_exc()
                if (
                    any_child_failed and children_spec
                    and children_spec.get("fail_parent_on_child_fail") and not force_fail
                ):
                    message = f"'{name}' failed because one or more {children_spec['role']}s failed"
                else:
                    message = f"Simulated failure while processing job '{name}'"

                span.set_tag("error.message", message)
                span.set_tag("error.stack", stack_trace)
                span.error = 1

                log.error(
                    "job failed", exc_info=True,
                    extra={"run_id": run_id, "job_name": name,
                           "job_namespace": namespace, "job_type": job_type},
                )

                models.mark_terminal(
                    run_id, "FAIL", ended_at=_iso(ended_at),
                    duration_seconds=ended_at - started_at, error_message=message,
                )
                _update_live(
                    request_id, run_id, status="FAIL", ended_at=ended_at,
                    duration_seconds=ended_at - started_at, error_message=message,
                )
                _safe_emit(
                    olc.emit_terminal,
                    client,
                    namespace=namespace, name=name, run_id=run_id,
                    job_type=job_type, ol_service=ol_service, state="FAIL",
                    run_facets={**run_facets, **
                                olc.error_facet(message, stack_trace)},
                )
                return {"run_id": run_id, "status": "FAIL", "error_message": message}

        models.mark_terminal(
            run_id, "COMPLETE", ended_at=_iso(ended_at), duration_seconds=ended_at - started_at,
        )
        _update_live(
            request_id, run_id, status="COMPLETE", ended_at=ended_at,
            duration_seconds=ended_at - started_at,
        )
        _safe_emit(
            olc.emit_terminal,
            client,
            namespace=namespace, name=name, run_id=run_id,
            job_type=job_type, ol_service=ol_service, state="COMPLETE",
            run_facets=run_facets,
        )
        log.info(
            "job completed", extra={"run_id": run_id, "job_name": name,
                                    "job_namespace": namespace, "job_type": job_type},
        )
        return {"run_id": run_id, "status": "COMPLETE", "error_message": None}
    finally:
        span.finish()


def simulate_request(form: dict) -> str:
    """Entry point called from the web UI. Dispatches a controller (which
    dispatches N workers, each of which dispatches M sub-tasks) on a
    background thread and returns a request_id the UI can poll.
    """
    request_id = str(uuid.uuid4())
    namespace = form.get("namespace") or config.OL_NAMESPACE
    controller_name = form.get(
        "controller_name") or "datadog.controller.request_handler"

    num_workers = int(form.get("num_workers", 3))
    controller_min = float(form.get("controller_duration_min", 5))
    controller_max = float(form.get("controller_duration_max", 15))
    controller_failure_rate = float(form.get("controller_failure_rate", 0))
    force_fail_controller = bool(form.get("force_fail_controller", False))
    fail_controller_on_worker_fail = bool(
        form.get("fail_controller_on_worker_fail", True))

    worker_min = float(form.get("worker_duration_min", 10))
    worker_max = float(form.get("worker_duration_max", 20))
    worker_failure_rate = float(form.get("worker_failure_rate", 0))
    force_fail_worker = bool(form.get("force_fail_worker", False))

    num_tasks = int(form.get("num_tasks", 2))
    task_min = float(form.get("task_duration_min", 60))
    task_max = float(form.get("task_duration_max", 120))
    task_failure_rate = float(form.get("task_failure_rate", 10))
    force_fail_task = bool(form.get("force_fail_task", False))
    fail_worker_on_task_fail = bool(form.get("fail_worker_on_task_fail", True))

    tasks_spec = None
    if num_tasks > 0:
        tasks_spec = {
            "count": num_tasks, "role": "task", "job_type": "TASK",
            "duration_min": task_min, "duration_max": task_max,
            "failure_rate": task_failure_rate, "force_fail": force_fail_task,
            "fail_parent_on_child_fail": fail_worker_on_task_fail,
            "children_spec": None,
        }

    workers_spec = None
    if num_workers > 0:
        workers_spec = {
            "count": num_workers, "role": "worker", "job_type": "TASK",
            "duration_min": worker_min, "duration_max": worker_max,
            "failure_rate": worker_failure_rate, "force_fail": force_fail_worker,
            "fail_parent_on_child_fail": fail_controller_on_worker_fail,
            "children_spec": tasks_spec,
        }

    with _live_lock:
        _live_requests[request_id] = {}

    thread = threading.Thread(
        target=_run_node,
        kwargs=dict(
            name=controller_name, namespace=namespace, job_type="JOB",
            duration_min=controller_min, duration_max=controller_max,
            failure_rate=controller_failure_rate, force_fail=force_fail_controller,
            request_id=request_id, run_id=olc.new_run_id(),
            parent=None, root=None, parent_trace_context=None,
            children_spec=workers_spec,
        ),
        daemon=True,
    )
    thread.start()
    return request_id


def get_scheduler_status() -> dict:
    with _scheduler_lock:
        s = dict(_scheduler_state)
    s.pop("stop_event", None)
    return s


def start_scheduler(form: dict, interval_seconds: float, duration_minutes: float = None) -> bool:
    """Start dispatching simulate_request(form) every interval_seconds, on
    a background thread, until stop_scheduler() is called or duration_minutes
    elapses (None/0 means run until stopped). Returns False if already running.
    """
    with _scheduler_lock:
        if _scheduler_state["running"]:
            return False

        stop_event = threading.Event()
        deadline = time.time() + duration_minutes * 60 if duration_minutes else None
        _scheduler_state.update({
            "running": True,
            "stop_event": stop_event,
            "started_at": time.time(),
            "dispatch_count": 0,
            "interval_seconds": interval_seconds,
            "deadline": deadline,
        })

    thread = threading.Thread(
        target=_scheduler_loop,
        args=(form, interval_seconds, deadline, stop_event),
        daemon=True,
    )
    thread.start()
    return True


def stop_scheduler() -> bool:
    with _scheduler_lock:
        if not _scheduler_state["running"]:
            return False
        _scheduler_state["stop_event"].set()
        _scheduler_state["running"] = False
        return True


def _scheduler_loop(form, interval_seconds, deadline, stop_event):
    while not stop_event.is_set():
        if deadline and time.time() >= deadline:
            break

        simulate_request(form)
        with _scheduler_lock:
            _scheduler_state["dispatch_count"] += 1

        stop_event.wait(interval_seconds)

    with _scheduler_lock:
        _scheduler_state["running"] = False


def _iso(ts):
    import datetime

    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).isoformat()
