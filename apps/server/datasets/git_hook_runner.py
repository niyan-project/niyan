import os
import sys

from datasets.git_push import GitPushDenied, enforce_pre_receive, record_post_receive


def main(hook_name, *, stdin=None, stderr=None):
    """Run one trusted server hook with bounded public diagnostics.

    Parameters
    ----------
    hook_name : str
        Supported Git hook operation.
    stdin : file-like object, optional
        Hook input override used by tests.
    stderr : file-like object, optional
        Sanitized diagnostic destination.

    Returns
    -------
    int
        Hook process exit status.
    """

    input_stream = stdin or sys.stdin
    error_stream = stderr or sys.stderr
    context_id = os.environ.get('NIYAN_PUSH_CONTEXT_ID', '')
    dataset_id = os.environ.get('NIYAN_DATASET_ID', '')
    if hook_name == 'pre-receive':
        try:
            enforce_pre_receive(context_id=context_id, dataset_id=dataset_id, lines=input_stream)
        except GitPushDenied as error:
            print(f'Niyān: {error}', file=error_stream)
            return 1
        except Exception:
            # Hook tracebacks, database errors, and Git internals must never enter receive-pack sideband output.
            print('Niyān: repository policy is temporarily unavailable. Retry niyan push.', file=error_stream)
            return 1
        return 0
    if hook_name == 'post-receive':
        try:
            record_post_receive(context_id=context_id, dataset_id=dataset_id, lines=input_stream)
        except Exception:
            # Git has already committed refs. Durable pre-receive proposals let maintenance reconcile this silently.
            return 0
        return 0
    return 1
