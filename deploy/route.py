"""Render Traefik file-provider configuration using deployment settings."""
import json
import os
import re
from urllib.parse import urlsplit

prefix = os.environ.get("CASE_PREFIX", "/case-investigator")
upstream = os.environ["TRAEFIK_UPSTREAM"]
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
