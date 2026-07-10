"""Flask web UI: trigger panel, live run view, persisted history, config banner."""
import logging
import socket

from flask import Flask, jsonify, render_template, request

from app import config, job_simulator, models

app = Flask(__name__)

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_MAGENTA = "\033[35m"


def _lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return None
    finally:
        s.close()


def _find_free_port(start_port, host="0.0.0.0", max_tries=20):
    """Vite-style auto-port: if start_port is taken, scan upward for a free
    one instead of hard-failing -- most useful for a demo tool where a
    previous run's process is often still lingering on the default port."""
    port = start_port
    for _ in range(max_tries):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return port
        except OSError:
            port += 1
        finally:
            s.close()
    # Give up scanning and let Flask itself raise a clear bind error.
    return start_port


def _print_startup_banner(port):
    local_url = f"http://127.0.0.1:{port}"
    lan_ip = _lan_ip()
    lan_url = f"http://{lan_ip}:{port}" if lan_ip else None
    port_note = f" {_YELLOW}(APP_PORT {config.APP_PORT} was in use){_RESET}" if port != config.APP_PORT else ""
    api_key_line = (
        f"{_GREEN}✓ set{_RESET}" if config.DD_API_KEY else f"{_YELLOW}✗ missing{_RESET}"
    )
    rum_enabled = bool(config.DD_RUM_APPLICATION_ID and config.DD_RUM_CLIENT_TOKEN)
    rum_line = f"{_GREEN}✓ enabled{_RESET}" if rum_enabled else f"{_DIM}disabled{_RESET}"

    # (plain label, colored value) -- label is padded on the plain text so
    # ANSI escape codes (added afterward) don't throw off column alignment.
    rows = [
        ("Local", f"{_CYAN}{local_url}{_RESET}{port_note}"),
    ]
    if lan_url:
        rows.append(("Network", f"{_CYAN}{lan_url}{_RESET}"))
    rows += [
        (None, None),
        ("Datadog site", f"{_MAGENTA}{config.DD_SITE}{_RESET}"),
        ("OL transport", config.OL_TRANSPORT),
        ("Log ship mode", config.LOG_SHIP_MODE),
        ("Service / env", f"{config.DD_SERVICE} / {config.DD_ENV}"),
        ("API key", api_key_line),
        ("RUM", rum_line),
    ]

    print()
    print(f"  {_BOLD}✈️  OpenLineage + APM + Logs Job Simulator{_RESET}")
    print()
    for label, value in rows:
        if label is None:
            print()
        else:
            print(f"  {_DIM}{label:<16}{_RESET} {value}")
    print()


@app.before_request
def _ensure_db():
    models.init_db()


@app.route("/")
def index():
    return render_template(
        "index.html",
        dd_site=config.DD_SITE,
        dd_service=config.DD_SERVICE,
        dd_env=config.DD_ENV,
        dd_rum_application_id=config.DD_RUM_APPLICATION_ID,
        dd_rum_client_token=config.DD_RUM_CLIENT_TOKEN,
    )


@app.route("/api/status")
def api_status():
    emit_status = job_simulator.get_last_emit_status()
    return jsonify(
        {
            "dd_site": config.DD_SITE,
            "ol_transport": config.OL_TRANSPORT,
            "log_ship_mode": config.LOG_SHIP_MODE,
            "dd_service": config.DD_SERVICE,
            "dd_env": config.DD_ENV,
            "api_key_set": bool(config.DD_API_KEY),
            "rum_enabled": bool(config.DD_RUM_APPLICATION_ID and config.DD_RUM_CLIENT_TOKEN),
            "last_emit_ok": emit_status["ok"],
            "last_emit_error": emit_status["error"],
            "last_emit_at": emit_status["at"],
        }
    )


@app.route("/api/simulate", methods=["POST"])
def api_simulate():
    form = request.get_json(force=True, silent=True) or {}
    request_id = job_simulator.simulate_request(form)
    return jsonify({"request_id": request_id})


@app.route("/api/live/<request_id>")
def api_live(request_id):
    runs = job_simulator.get_live_request(request_id)
    return jsonify({"runs": runs})


@app.route("/api/live")
def api_live_all():
    return jsonify({"runs": job_simulator.get_all_live_runs()})


@app.route("/api/history")
def api_history():
    return jsonify({"history": models.list_history()})


@app.route("/api/schedule/start", methods=["POST"])
def api_schedule_start():
    body = request.get_json(force=True, silent=True) or {}
    interval_seconds = float(body.pop("interval_seconds", 30) or 30)
    duration_minutes = body.pop("duration_minutes", None)
    duration_minutes = float(duration_minutes) if duration_minutes else None
    started = job_simulator.start_scheduler(body, interval_seconds, duration_minutes)
    return jsonify({"started": started, **job_simulator.get_scheduler_status()})


@app.route("/api/schedule/stop", methods=["POST"])
def api_schedule_stop():
    stopped = job_simulator.stop_scheduler()
    return jsonify({"stopped": stopped, **job_simulator.get_scheduler_status()})


@app.route("/api/schedule/status")
def api_schedule_status():
    return jsonify(job_simulator.get_scheduler_status())


def main():
    models.init_db()
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    port = _find_free_port(config.APP_PORT)
    _print_startup_banner(port)
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
