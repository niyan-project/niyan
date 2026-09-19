import unittest

from niyan.filesystem.path import ParsedLocation, canonical_name, options_from_url, parse_location, strip_protocol


class FilesystemPathTests(unittest.TestCase):
    """Verify canonical fsspec URLs remain strict and reproducible."""

    def test_canonical_and_configured_locations_resolve(self):
        """Parse encoded paths, revisions, configured hosts, and dataset prefixes."""

        self.assertEqual(parse_location('niyan://data.example.test/lab/images/raw/a%20b.csv?revision=release%2Fv1'), ParsedLocation(host='https://data.example.test', locator_path='lab/images/raw/a b.csv', revision='release/v1'))
        self.assertEqual(parse_location('raw/a.csv', configured_host='data.example.test', configured_dataset='/lab/images/', configured_revision='main'), ParsedLocation(host='https://data.example.test', locator_path='lab/images/raw/a.csv', revision='main'))
        self.assertEqual(parse_location('', configured_dataset='lab/images'), ParsedLocation(host=None, locator_path='lab/images', revision=None))

    def test_location_parser_rejects_ambiguous_or_unsafe_inputs(self):
        """Reject malformed authorities, fragments, queries, and path traversal."""

        cases = [
            (7, {}),
            ('niyan:///lab/images', {}),
            ('niyan://data.example.test/lab/images#fragment', {}),
            ('lab/images#fragment', {}),
            ('lab/images?other=value', {}),
            ('lab/images?revision=one&revision=two', {}),
            ('lab/images?revision=', {}),
            ('lab/images?revision=main', {'configured_revision': 'other'}),
            ('niyan://one.example/lab/images', {'configured_host': 'two.example'}),
            ('niyan://data.example.test/other/data', {'configured_dataset': 'lab/images'}),
            ('', {}),
            ('lab/../images', {}),
            ('lab//images', {}),
        ]
        for value, options in cases:
            with self.subTest(value=value, options=options):
                with self.assertRaises((TypeError, ValueError)):
                    parse_location(value, **options)

    def test_protocol_helpers_preserve_authority_and_safe_options(self):
        """Expose fsspec construction data without embedding credentials."""

        canonical = 'niyan://data.example.test/lab/images/data.csv?revision=abc123'
        self.assertEqual(strip_protocol(canonical), canonical)
        self.assertEqual(strip_protocol(f'niyan::{canonical}'), canonical)
        self.assertEqual(strip_protocol('lab/images'), 'lab/images')
        self.assertEqual(options_from_url(canonical), {'host': 'https://data.example.test', 'revision': 'abc123'})
        self.assertEqual(options_from_url('lab/images'), {})
        self.assertEqual(options_from_url('niyan://data.example.test/lab/images?other=value'), {'host': 'https://data.example.test'})
        self.assertEqual(options_from_url('niyan://localhost:8000/lab/images'), {'host': 'http://localhost:8000'})

    def test_canonical_name_pins_commit_and_encodes_path(self):
        """Build a reusable authority-preserving exact-revision URL."""

        result = canonical_name(host='https://data.example.test', dataset_path='/lab/images/', repository_path='/raw/a b.csv/', resolved_commit='a' * 40)

        self.assertEqual(result, f'niyan://data.example.test/lab/images/raw/a%20b.csv?revision={"a" * 40}')


if __name__ == '__main__':
    unittest.main()
