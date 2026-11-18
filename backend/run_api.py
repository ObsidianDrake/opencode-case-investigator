import argparse
import logging
import os

from waitress import serve

from app import create_app
from service_logs import configure_logging
from settings import load_settings

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", help="Path to config.yml (default: current directory)")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        settings = load_settings(args.config)
        if not settings.get("CASE_STATE"):
            raise ValueError("Missing configuration: server.state_dir")
        configure_logging(settings)
        logging.info("Initializing database and application")
        app = create_app(settings)
    except (ValueError, OSError) as error:
        parser.exit(2, "Configuration error: %s\n" % error)
    logging.info("Starting case investigator at %s:%s%s", settings.get("BIND_HOST", "127.0.0.1"),
                 settings.get("PORT", 8000), settings.get("CASE_PREFIX", "/case-investigator"))
    serve(app, host=settings.get("BIND_HOST", "127.0.0.1"), port=int(settings.get("PORT", 8000)),
          threads=12, max_request_body_size=6*1024*1024)
