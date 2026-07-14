"""Structured JSON logging. LOG_SHIP_MODE selects "agent" (stdout, tailed by
the Datadog Agent) or "http" (shipped directly to the Logs API)."""
import logging
import json
import socket
import threading
import time
import queue

from app import config


class JsonFormatter(logging.Formatter):
    """build_payload() returns the dict (used for the HTTP API path, as
    top-level attributes); format() JSON-encodes it (used for stdout)."""

    def build_payload(self, record: logging.LogRecord) -> dict:
        # dd.service (from ddtrace's automatic log injection) reflects the
        # tracer's global default service, not a per-span override -- our
        # job spans set service=ol_service (e.g. "<service>-controller"),
        # which is also what the OpenLineage "tags" facet's _dd.ol_service
        # tells Jobs Monitoring to key on. Read it straight off the active
        # span so a run's logs carry the same service its job run does.
        from ddtrace import tracer

        span = tracer.current_span()
        service = span.service if span and span.service else config.DD_SERVICE

        payload = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": service,
            "env": config.DD_ENV,
        }

        for attr in ("run_id", "job_name", "job_namespace", "job_type"):
            if hasattr(record, attr):
                payload[attr] = getattr(record, attr)

        # dd_trace_id_override/dd_span_id_override let a specific log call
        # supply its own correlation ids instead of the active span's --
        # used to test tagging a log with Jobs Monitoring's synthetic id
        # (see job_simulator.py's _jobs_monitoring_id) alongside the normal
        # real-trace-correlated log line.
        dd_trace_id = getattr(record, "dd_trace_id_override", None) or getattr(record, "dd.trace_id", None)
        dd_span_id = getattr(record, "dd_span_id_override", None) or getattr(record, "dd.span_id", None)
        if dd_trace_id:
            payload["dd.trace_id"] = dd_trace_id
        if dd_span_id:
            payload["dd.span_id"] = dd_span_id

        if record.exc_info:
            payload["error.stack"] = self.formatException(record.exc_info)
            payload["error.message"] = str(record.exc_info[1])
            payload["error.kind"] = record.exc_info[0].__name__

        return payload

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(self.build_payload(record))


class DatadogHttpLogHandler(logging.Handler):
    """Fire-and-forget async shipper to Datadog's Logs API via datadog-api-client."""

    def __init__(self, formatter: JsonFormatter):
        super().__init__()
        self._json_formatter = formatter
        self._hostname = socket.gethostname()
        self._q: "queue.Queue[dict]" = queue.Queue(maxsize=1000)
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._q.put_nowait(self._json_formatter.build_payload(record))
        except queue.Full:
            pass
        except Exception:
            self.handleError(record)

    def _worker(self):
        from datadog_api_client import ApiClient, Configuration
        from datadog_api_client.v2.api.logs_api import LogsApi
        from datadog_api_client.v2.model.http_log import HTTPLog
        from datadog_api_client.v2.model.http_log_item import HTTPLogItem

        with ApiClient(Configuration()) as api_client:
            api = LogsApi(api_client)
            while True:
                payload = self._q.get()
                try:
                    message = payload.pop("message", "")
                    api.submit_log(
                        HTTPLog(
                            [
                                HTTPLogItem(
                                    message=message,
                                    ddsource="python",
                                    ddtags=f"env:{config.DD_ENV}",
                                    hostname=self._hostname,
                                    **payload,
                                )
                            ]
                        )
                    )
                except Exception:
                    pass


def configure_logging():
    root = logging.getLogger()
    root.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))
    root.handlers.clear()

    formatter = JsonFormatter()

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    if config.LOG_SHIP_MODE == "http":
        if not config.DD_API_KEY:
            root.warning("LOG_SHIP_MODE=http but DD_API_KEY is not set; logs will not ship")
        else:
            root.addHandler(DatadogHttpLogHandler(formatter))

    # Keep ddtrace/openlineage's own internal logging off our pipeline --
    # they still print via their own default handlers.
    logging.getLogger("ddtrace").propagate = False
    logging.getLogger("openlineage").propagate = False

    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
