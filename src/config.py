"""Config and secrets loading.

Non-secret settings load from ``config.json`` (committed). Secrets load from
the gitignored ``.env`` file via ``os.getenv()`` — never from source or config
(D-06/D-07, Pitfall 4/9).
"""

import json
import os

try:
    from dotenv import load_dotenv

    load_dotenv()  # loads .env into os.environ
except ImportError:
    pass


def load_settings(config_path="config.json"):
    """Merge config.json + .env into a Settings dict.

    Returns a dict of the raw config.json contents merged with ``login`` and
    ``password`` read from the environment. Secrets come from env exclusively
    and have no fallback defaults.
    """
    with open(config_path, encoding="utf-8") as f:
        raw = json.load(f)
    return {
        **raw,
        "login": os.getenv("PRIVRATNIK_LOGIN"),
        "password": os.getenv("PRIVRATNIK_PASSWORD"),
    }
