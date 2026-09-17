import argparse
import sys
from pathlib import Path

from niyan import __version__
from niyan.auth import login, resolve_host
from niyan.config import AppPaths
from niyan.credentials import CredentialStores
from niyan.errors import NiyanCliError
from niyan.git import clone_dataset, credential_helper


def build_parser():
    """Build the initial public CLI command grammar."""

    parser = argparse.ArgumentParser(prog='niyan', description='Work with Niyān dataset repositories.')
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    commands = parser.add_subparsers(dest='command', required=True)

    auth_parser = commands.add_parser('auth', help='Authenticate to a Niyān installation.')
    auth_commands = auth_parser.add_subparsers(dest='auth_command', required=True)
    login_parser = auth_commands.add_parser('login', help='Authenticate through a browser-assisted device flow.')
    login_parser.add_argument('hostname', nargs='?', help='Installation hostname or origin. Defaults to NIYAN_HOST or configured host.')
    login_parser.add_argument('--local', action='store_true', help='Use this credential only inside the current Git checkout.')
    login_parser.add_argument('--dataset', help='Restrict the server token and local binding to one namespace/dataset path.')
    login_parser.add_argument('--read-only', action='store_true', help='Request read-only API and repository scopes.')
    login_parser.add_argument('--insecure-storage', action='store_true', help='Explicitly store the token in a mode-0600 plaintext user file.')
    login_parser.add_argument('--no-browser', action='store_true', help='Print verification instructions without opening a browser.')

    dataset_parser = commands.add_parser('dataset', help='Work with dataset repositories.')
    dataset_commands = dataset_parser.add_subparsers(dest='dataset_command', required=True)
    clone_parser = dataset_commands.add_parser('clone', help='Clone a dataset through Niyān-managed Git authentication.')
    clone_parser.add_argument('dataset_path', help='Dataset path in namespace/dataset form.')
    clone_parser.add_argument('destination', nargs='?', help='Checkout destination. Defaults to the dataset slug.')
    clone_parser.add_argument('--host', help='Installation hostname or origin. Defaults to NIYAN_HOST or configured host.')

    return parser


def build_credential_helper_parser():
    """Build the private argument grammar invoked only by Git."""

    parser = argparse.ArgumentParser(prog='niyan _credential-helper')
    parser.add_argument('operation', choices=('get', 'store', 'erase'))
    parser.add_argument('--host', required=True)
    parser.add_argument('--username', required=True)
    parser.add_argument('--token-id')
    parser.add_argument('--storage', choices=('keyring', 'file'))
    parser.add_argument('--token-file')
    return parser


def main(argv=None):
    """Run the CLI and translate expected failures into concise diagnostics.

    Parameters
    ----------
    argv : list[str], optional
        Arguments excluding the executable name.

    Returns
    -------
    int
        Process exit status.
    """

    command_line = list(sys.argv[1:] if argv is None else argv)
    internal_helper = bool(command_line and command_line[0] == '_credential-helper')
    parser = build_credential_helper_parser() if internal_helper else build_parser()
    arguments = parser.parse_args(command_line[1:] if internal_helper else command_line)
    paths = AppPaths.from_environment()
    stores = CredentialStores(paths=paths)
    try:
        if internal_helper:
            if arguments.operation != 'get':
                return 0
            if arguments.token_file and (arguments.token_id or arguments.storage):
                parser.error('--token-file cannot be combined with stored credential arguments.')
            if not arguments.token_file and not (arguments.token_id and arguments.storage):
                parser.error('stored credential helper use requires --token-id and --storage.')
            return credential_helper(
                host=arguments.host,
                username=arguments.username,
                token_id=arguments.token_id,
                storage=arguments.storage,
                token_file=arguments.token_file,
                paths=paths,
                stores=stores,
            )
        if arguments.command == 'auth' and arguments.auth_command == 'login':
            host = resolve_host(arguments.hostname, paths=paths, cwd=Path.cwd())
            login(
                host=host,
                paths=paths,
                stores=stores,
                local=arguments.local,
                dataset_path=arguments.dataset,
                read_only=arguments.read_only,
                insecure_storage=arguments.insecure_storage,
                no_browser=arguments.no_browser,
                cwd=Path.cwd(),
            )
            return 0
        if arguments.command == 'dataset' and arguments.dataset_command == 'clone':
            host = resolve_host(arguments.host, paths=paths, cwd=Path.cwd())
            clone_dataset(
                host=host,
                dataset_path=arguments.dataset_path,
                destination=arguments.destination,
                paths=paths,
                stores=stores,
                cwd=Path.cwd(),
            )
            return 0
        parser.error('Unsupported command.')
    except NiyanCliError as error:
        print(f'error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
