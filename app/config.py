"""Environment-driven configuration for the demo app."""
import os
import sys

from dotenv import load_dotenv

load_dotenv()


def _bool(val: str, default: bool = False) -> bool:
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


DD_API_KEY = os.environ.get("DD_API_KEY", "").strip()
DD_SITE = os.environ.get("DD_SITE", "datadoghq.com").strip()
OL_TRANSPORT = os.environ.get("OL_TRANSPORT", "datadog").strip().lower()
OL_NAMESPACE = os.environ.get("OL_NAMESPACE", "demo.datadog").strip()
OL_PRODUCER = os.environ.get(
    "OL_PRODUCER", "https://github.com/datadog/openlineage-do-jobs"
).strip()
DD_SERVICE = os.environ.get("DD_SERVICE", "openlineage-worker-demo").strip()
DD_ENV = os.environ.get("DD_ENV", "demo").strip()
LOG_SHIP_MODE = os.environ.get("LOG_SHIP_MODE", "agent").strip().lower()
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
APP_PORT = int(os.environ.get("APP_PORT", "8080"))

# RUM is optional -- the UI only initializes it when both are set.
DD_RUM_APPLICATION_ID = os.environ.get("DD_RUM_APPLICATION_ID", "").strip()
DD_RUM_CLIENT_TOKEN = os.environ.get("DD_RUM_CLIENT_TOKEN", "").strip()

DB_PATH = os.environ.get("DB_PATH", os.path.join(
    os.path.dirname(__file__), "..", "demo.db"))


def require_api_key():
    if not DD_API_KEY:
        sys.stderr.write(
            "\nERROR: DD_API_KEY is not set.\n"
            "Set it in your environment or in a .env file (see .env.example).\n\n"
        )
        sys.exit(1)
