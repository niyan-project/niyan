import io
import json
import os
import runpy
from unittest.mock import patch

from django.test import SimpleTestCase

from datasets.git_hook_runner import main
from datasets.git_push import GitPushDenied


class GitHookRunnerTests(SimpleTestCase):
    """Verify trusted Git hook processes fail closed without leaking internals."""

    def test_pre_receive_forwards_context_and_sanitizes_failures(self):
        """Accept valid updates and distinguish policy denials from outages."""

        environment = {'NIYAN_PUSH_CONTEXT_ID': 'context-id', 'NIYAN_DATASET_ID': 'dataset-id'}
        with patch.dict(os.environ, environment, clear=False), patch('datasets.git_hook_runner.enforce_pre_receive') as enforce:
            self.assertEqual(main('pre-receive', stdin=io.StringIO('old new refs/heads/main\n'), stderr=io.StringIO()), 0)
        self.assertEqual(enforce.call_args.kwargs['context_id'], 'context-id')
        self.assertEqual(enforce.call_args.kwargs['dataset_id'], 'dataset-id')

        for error, message in ((GitPushDenied('protected ref'), 'Niyān: protected ref\n'), (RuntimeError('private database detail'), 'Niyān: repository policy is temporarily unavailable. Retry niyan push.\n')):
            with self.subTest(error=type(error).__name__):
                output = io.StringIO()
                with patch('datasets.git_hook_runner.enforce_pre_receive', side_effect=error):
                    self.assertEqual(main('pre-receive', stdin=io.StringIO(), stderr=output), 1)
                self.assertEqual(output.getvalue(), message)

    def test_post_receive_is_best_effort_and_unknown_hooks_fail(self):
        """Never roll back committed refs, but reject unsupported hook names."""

        with patch('datasets.git_hook_runner.record_post_receive') as record:
            self.assertEqual(main('post-receive', stdin=io.StringIO('old new refs/heads/main\n')), 0)
        record.assert_called_once()

        with patch('datasets.git_hook_runner.record_post_receive', side_effect=RuntimeError('private detail')):
            self.assertEqual(main('post-receive', stdin=io.StringIO()), 0)
        self.assertEqual(main('update', stdin=io.StringIO()), 1)

    def test_hook_settings_are_derived_from_bounded_environment(self):
        """Load the isolated hook settings from explicitly exported values."""

        database = {'ENGINE': 'django.db.backends.postgresql', 'NAME': 'niyan'}
        environment = {
            'NIYAN_HOOK_DATABASE_CONFIG': json.dumps(database),
            'NIYAN_HOOK_REPOSITORIES_ROOT': '/srv/niyan/git',
            'NIYAN_GIT_PUSH_CONTEXT_LIFETIME_SECONDS': '300',
            'NIYAN_GIT_PUSH_LEASE_LIFETIME_SECONDS': '900',
        }
        with patch.dict(os.environ, environment, clear=False):
            settings = runpy.run_module('project.hook_settings', run_name='phase8_hook_settings')

        self.assertEqual(settings['DATABASES'], {'default': database})
        self.assertEqual(str(settings['REPOSITORIES_ROOT']), '/srv/niyan/git')
        self.assertEqual(settings['NIYAN_GIT_PUSH_CONTEXT_LIFETIME_SECONDS'], 300)
        self.assertEqual(settings['NIYAN_GIT_PUSH_LEASE_LIFETIME_SECONDS'], 900)
