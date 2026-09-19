import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    """Format operational logs without serializing request or secret data."""

    def format(self, record):
        """Return one stable JSON object for a log record.

        Parameters
        ----------
        record : logging.LogRecord
            Standard-library log record.

        Returns
        -------
        str
            Single-line JSON suitable for container log collectors.
        """

        payload = {
            'timestamp': datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }
        status_code = getattr(record, 'status_code', None)
        if isinstance(status_code, int):
            payload['status_code'] = status_code
        if record.exc_info:
            payload['exception'] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
