"""Structured JSON logging with ddtrace trace/span injection.

Two shipping modes (LOG_SHIP_MODE):
  - "agent": logs go to stdout in JSON; the Datadog Agent tails/collects them.
    The Agent's JSON log processing promotes each key in the JSON message to
    a top-level facet automatically, so trace correlation works from the
    nested `dd.trace_id`/`dd.span_id` keys.
  - "http": no Agent present, so logs are submitted directly to Datadog's
    Logs API. There is no Agent-side JSON parsing step in this path, so
    `dd.trace_id`/`dd.span_id` (and everything else) must be sent as actual
    top-level attributes of the log event -- burying them inside a JSON
    string under `message` leaves them invisible to trace-log correlation,
    even though the value itself is technically present somewhere in the
    payload.
"""
import logging
import json
import socket
import threading
import time
import queue

from app import config


class JsonFormatter(logging.Formatter):
    """Builds the structured log payload. `format()` (used for the stdout/
    agent path) JSON-encodes it; `build_payload()` (used for the direct HTTP
    API path) returns the dict so its keys can be sent as top-level log
    attributes instead of nested inside a message string.
    """

    def build_payload(self, record: logging.LogRecord) -> dict:
        payload = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": config.DD_SERVICE,
            "env": config.DD_ENV,
        }

        for attr in ("run_id", "job_name", "job_namespace", "job_type"):
            if hasattr(record, attr):
                payload[attr] = getattr(record, attr)

        # Always compute from the active span directly rather than trusting
        # a pre-existing record.dd.trace_id -- ddtrace's own automatic log
        # injection (active under ddtrace-run + DD_LOGS_INJECTION=true) sets
        # that attribute itself via format_trace_id(), which hex-encodes any
        # trace ID above 64 bits. That hex value doesn't match anything: APM
        # displays/searches trace IDs as the plain-decimal low 64 bits even
        # for a 128-bit trace ID, so a hex-encoded id looks up as "does not
        # exist." Masking to the low 64 bits ourselves is what actually
        # matches the ID shown in the Datadog UI.
        dd_trace_id = None
        dd_span_id = None
        try:
            from ddtrace import tracer

            span = tracer.current_span()
            if span is not None:
                dd_trace_id = str(span.trace_id & 0xFFFFFFFFFFFFFFFF)
                dd_span_id = str(span.span_id)
        except Exception:
            pass

        if dd_trace_id is None:
            dd_trace_id = getattr(record, "dd.trace_id", None)
        if dd_span_id is None:
            dd_span_id = getattr(record, "dd.span_id", None)

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
    """Fire-and-forget async shipper to Datadog's Logs API, via the official
    datadog-api-client (Configuration() reads DD_API_KEY/DD_SITE from the
    environment directly, same variable names this app already uses).

    Submits each log line as its own single-item HTTPLog batch -- the Logs
    intake API requires a JSON array of log objects even for one line; a bare
    object (what a naive `requests.post(json=payload)` would send) is
    silently rejected. Every JsonFormatter field is passed as a top-level
    HTTPLogItem attribute (HTTPLogItem accepts arbitrary extra kwargs, even
    non-identifier keys like "dd.trace_id"), not nested inside `message` --
    trace-log correlation looks for `dd.trace_id`/`dd.span_id` as attributes
    of the log event itself.
    """

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
    root.setLevel(logging.INFO)
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

    # Attaching handlers to the root logger means EVERY logger in the
    # process funnels through them by default -- including ddtrace's own
    # internal diagnostics (span lifecycle debug logs, its background
    # writer's connection errors, etc.) and openlineage-python's init
    # messages. None of those run inside one of our job spans, so they
    # legitimately report dd.trace_id=0 -- correct for them, but it pollutes
    # the shipped demo log stream with noise that looks like a correlation
    # bug. Keep them off our custom pipeline; they still print via their own
    # default logging behavior (e.g. DD_TRACE_DEBUG output to stderr).
    logging.getLogger("ddtrace").propagate = False
    logging.getLogger("openlineage").propagate = False

    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
