"""
JSON log lines for production (VFX-31): one object per line, easy to search and ship.
"""

import json
import logging
import re
from datetime import datetime, timezone

# Activation and password reset links carry a one-time token; never write it to the log.
ACCOUNT_LINK = re.compile(r"/(activate|password_confirm)/[^/\s]+/[^/\s]+/?")


def redact(message):
    return ACCOUNT_LINK.sub(r"/\1/<redacted>/", message)


class JsonFormatter(logging.Formatter):
    """
    Format a log record as a single JSON object.
    """

    def format(self, record):
        entry = {
            "time": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        if record.exc_info:
            entry["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(entry, ensure_ascii=False)
