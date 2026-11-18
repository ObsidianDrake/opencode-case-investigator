"""Read deployment settings shared by the service and Traefik route generator."""
import os
from pathlib import Path

import yaml


# Keep the legacy environment names at the boundary for existing deployments.
FIELDS = {
    "server": {"state_dir": "CASE_STATE", "public_origin": "PUBLIC_ORIGIN", "prefix": "CASE_PREFIX",
               "bind_host": "BIND_HOST", "port": "PORT", "session_secret": "SESSION_SECRET"},
    "gitea": {"url": "GITEA_BASE", "token": "GITEA_TOKEN", "client_id": "GITEA_CLIENT_ID", "client_secret": "GITEA_CLIENT_SECRET"},
    "opencode": {"url": "OPENCODE_BASE", "username": "OPENCODE_SERVER_USERNAME",
                 "password": "OPENCODE_SERVER_PASSWORD"},
    "traefik": {"upstream": "TRAEFIK_UPSTREAM"},
}


def string_list(value, field, allow_empty=False):
    if (not isinstance(value, list) or (not value and not allow_empty)
            or any(not isinstance(item, str) or not item.strip() for item in value)):
        raise ValueError("%s requires a list of non-empty strings" % field)
    return [item.strip() for item in value]


def load_settings(path=None):
    filename = Path(path) if path is not None else Path("config.yml")
    try:
        with filename.open(encoding="utf-8") as stream:
            data = yaml.safe_load(stream)
    except FileNotFoundError:
        if path is None and ("CASE_STATE" in os.environ or "TRAEFIK_UPSTREAM" in os.environ):
            return dict(os.environ)
        raise ValueError("Configuration file not found: %s" % filename) from None
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        location = " at line %d" % (mark.line + 1) if mark else ""
        raise ValueError("Invalid YAML in %s%s" % (filename, location)) from None
    if not isinstance(data, dict):
        raise ValueError("Configuration must be a YAML mapping")
    settings = {}
    for section, fields in FIELDS.items():
        values = data.get(section, {})
        if not isinstance(values, dict):
            raise ValueError("%s requires a mapping" % section)
        for key, name in fields.items():
            if key in values:
                value = values[key]
                if key == "port":
                    if type(value) is not int or not 1 <= value <= 65535:
                        raise ValueError("server.port must be an integer between 1 and 65535")
                elif not isinstance(value, str):
                    raise ValueError("%s.%s must be a string" % (section, key))
                settings[name] = value
    policy = data.get("access_policy")
    if not isinstance(policy, dict):
        raise ValueError("access_policy requires a mapping")
    if set(policy) - {"account_email_domains", "excluded_account_prefixes"}:
        raise ValueError("Unknown access_policy field (expected account_email_domains or excluded_account_prefixes)")
    settings["INTERNAL_DOMAINS"] = string_list(policy.get("account_email_domains"),
                                              "access_policy.account_email_domains")
    settings["EXCLUDED_PREFIXES"] = string_list(policy.get("excluded_account_prefixes", []),
                                               "access_policy.excluded_account_prefixes", allow_empty=True)
    return settings


def internal_email(email, domains, prefixes):
    if not isinstance(email, str) or email.count("@") != 1:
        return False
    account, domain = (part.strip().lower() for part in email.split("@"))
    return bool(account and domain in domains and not account.startswith(prefixes))
