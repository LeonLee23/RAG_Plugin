import json
import logging
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {'timestamp': datetime.now(timezone.utc).isoformat(), 'level': record.levelname,
                   'event': record.getMessage()}
        for key in ('request_id', 'kb_id', 'document_id', 'attempt', 'stage', 'elapsed_ms', 'error_code'):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure():
    logger = logging.getLogger('app')
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
