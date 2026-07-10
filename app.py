"""Entry point. Run with: ddtrace-run python app.py"""
from app.logging_setup import configure_logging
from app.web import main

configure_logging()

if __name__ == "__main__":
    main()
