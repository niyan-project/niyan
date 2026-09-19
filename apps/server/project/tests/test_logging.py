import json
import logging

from django.test import SimpleTestCase

from project.logging import JsonFormatter


class JsonFormatterTests(SimpleTestCase):
    """Keep production log structure useful and secret-safe."""

    def test_formatter_emits_bounded_fields(self):
        """Ignore arbitrary record attributes such as request headers."""

        record = logging.LogRecord('niyan.test', logging.INFO, __file__, 1, 'ready %s', ('now',), None)
        record.request_headers = {'Authorization': 'Bearer secret'}
        record.status_code = 200

        payload = json.loads(JsonFormatter().format(record))

        self.assertEqual(payload['message'], 'ready now')
        self.assertEqual(payload['status_code'], 200)
        self.assertNotIn('request_headers', payload)
        self.assertNotIn('secret', json.dumps(payload))
