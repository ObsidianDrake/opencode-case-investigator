"""Render Traefik file-provider configuration using deployment settings."""
import json
import argparse
import sys
from pathlib import Path
import re
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from settings import load_settings

parser = argparse.ArgumentParser()
parser.add_argument("--config", help="Path to config.yml (default: current directory)")
try:
    settings = load_settings(parser.parse_args().config)
except (ValueError, OSError) as error:
    parser.exit(2, "Configuration error: %s\n" % error)

prefix = settings.get("CASE_PREFIX", "/case-investigator")
upstream = settings.get("TRAEFIK_UPSTREAM", "")
if not re.fullmatch(r"(/[A-Za-z0-9_-]+)+", prefix):
    raise SystemExit("Invalid CASE_PREFIX")
if urlsplit(upstream).scheme not in ("http", "https") or not urlsplit(upstream).netloc:
    raise SystemExit("Invalid TRAEFIK_UPSTREAM")
rule = "Path(`%s`) || PathPrefix(`%s/`)" % (prefix, prefix)
print("[http.routers.case-investigator]")
print("rule =", json.dumps(rule))
print('service = "case-investigator"')
print("[[http.services.case-investigator.loadBalancer.servers]]")
print("url =", json.dumps(upstream))
