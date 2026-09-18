import base64
import hashlib
import os
from unittest import skipUnless
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from django.conf import settings
from django.test import SimpleTestCase

from datasets.object_storage import S3ObjectStore


RUN_S3_SMOKE_TEST = os.environ.get('NIYAN_RUN_S3_SMOKE_TEST') == '1'


@skipUnless(RUN_S3_SMOKE_TEST, 'Set NIYAN_RUN_S3_SMOKE_TEST=1 to exercise the configured private S3-compatible backend.')
class S3DataPlaneSmokeTests(SimpleTestCase):
    """Exercise one real direct transfer without making it a suite dependency."""

    def test_signed_upload_metadata_download_and_delete(self):
        """Round-trip a tiny private object through the configured S3 adapter."""

        store = S3ObjectStore(settings.NIYAN_S3_CONFIGURATION)
        content = b'Niyan S3-compatible smoke test\n'
        oid = hashlib.sha256(content).hexdigest()
        relative_key = f'smoke-tests/{uuid4()}/{oid}'
        checksum = base64.b64encode(bytes.fromhex(oid)).decode('ascii') if store.supports_sha256_checksums else None
        try:
            upload = store.presign_upload(relative_key, size=len(content), expires_in=60, checksum_sha256=checksum)
            self._request(upload.url, method=upload.method, headers=upload.headers, data=content)
            stored = store.head(relative_key)
            download = store.presign_download(relative_key, expires_in=60)
            downloaded = self._request(download.url, method=download.method, headers=download.headers)

            self.assertEqual(stored.size, len(content))
            self.assertEqual(downloaded, content)
        finally:
            store.delete(relative_key)

    def _request(self, url, *, method, headers, data=None):
        """Perform a signed request without exposing its credential-bearing URL."""

        try:
            with urlopen(Request(url, method=method, headers=headers, data=data), timeout=30) as response:
                return response.read()
        except URLError as error:
            raise AssertionError(f'The configured S3-compatible backend rejected the signed {method} smoke-test request ({type(error).__name__}).') from None
