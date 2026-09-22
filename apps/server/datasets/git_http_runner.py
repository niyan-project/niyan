"""Bounded request-body adapter for chunked Git receive-pack requests."""

import os
import sys
import tempfile


COPY_CHUNK_BYTES = 64 * 1024


def spool_stdin(*, maximum_bytes):
    """Copy standard input to an anonymous temporary file with a hard limit.

    Parameters
    ----------
    maximum_bytes : int
        Maximum decoded request size accepted by the server.

    Returns
    -------
    tuple[typing.BinaryIO, int]
        Rewound temporary file and its exact byte length.

    Raises
    ------
    ValueError
        If the request exceeds the configured limit.
    """

    temporary_file = tempfile.TemporaryFile(mode='w+b')
    length = 0
    try:
        while chunk := sys.stdin.buffer.read(COPY_CHUNK_BYTES):
            length += len(chunk)
            if length > maximum_bytes:
                raise ValueError('Git request exceeds the configured limit.')
            temporary_file.write(chunk)
        temporary_file.seek(0)
        return temporary_file, length
    except Exception:
        temporary_file.close()
        raise


def main(arguments=None):
    """Spool one unknown-length request, then replace this process with Git."""

    arguments = list(arguments if arguments is not None else sys.argv[1:])
    if len(arguments) < 2:
        raise SystemExit('usage: git_http_runner.py MAXIMUM_BYTES COMMAND [ARG ...]')
    try:
        maximum_bytes = int(arguments[0])
        temporary_file, length = spool_stdin(maximum_bytes=maximum_bytes)
    except (OSError, ValueError):
        sys.stdout.buffer.write(b'Status: 413 Content Too Large\r\nContent-Type: text/plain\r\n\r\nGit request exceeds the configured limit.\n')
        return 0

    environment = os.environ.copy()
    environment['CONTENT_LENGTH'] = str(length)
    os.dup2(temporary_file.fileno(), sys.stdin.fileno())
    os.execvpe(arguments[1], arguments[1:], environment)


if __name__ == '__main__':
    raise SystemExit(main())
