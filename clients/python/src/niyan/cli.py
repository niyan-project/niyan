import argparse
import getpass
import sys
from pathlib import Path

from niyan import __version__
from niyan.auth import authentication_status, login, login_with_token, logout, resolve_host
from niyan.cache import prune_cache, show_cache_status
from niyan.config import AppPaths
from niyan.credentials import CredentialStores
from niyan.datasets import create_remote_dataset, delete_remote_dataset, edit_dataset_access, edit_remote_dataset, grant_dataset_access, list_dataset_access, list_remote_datasets, revoke_dataset_access, view_remote_dataset
from niyan.errors import ApiError, ConfigurationError, CredentialError, GitConflictError, GitDependencyError, NiyanCliError
from niyan.git import clone_dataset, credential_helper, fetch_dataset, pull_dataset, push_dataset
from niyan.lfs_transfer import run_lfs_transfer
from niyan.project_dependencies import add_dataset_dependency, remove_dataset_dependency, show_dataset_dependencies, update_dataset_dependencies
from niyan.remote_files import download_remote_paths, show_remote_tree, stream_remote_file
from niyan.working_copy import create_branch, create_tag, commit_changes, delete_branch, delete_tag, list_branches, list_tags, merge_revision, rename_branch, restore_paths, show_diff, show_log, show_status, stage_paths, switch_branch


def _add_access_target_arguments(parser):
    """Add the common dataset and host selectors to an access command."""

    parser.add_argument('dataset_path', nargs='?', help='Dataset path in namespace/dataset form. Defaults to the current Niyān checkout.')
    parser.add_argument('--host', help='Installation hostname or origin. Defaults to NIYAN_HOST or configured host.')


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
    login_parser.add_argument('--with-token', action='store_true', help='Read one manually issued access token from standard input.')

    status_parser = auth_commands.add_parser('status', help='Inspect the selected credential and verify it with the server.')
    status_parser.add_argument('--host', help='Installation hostname or origin. Defaults to NIYAN_HOST or configured host.')
    status_parser.add_argument('--dataset', help='Select an exact dataset-bound credential before a user-level credential.')

    logout_parser = auth_commands.add_parser('logout', help='Revoke and remove one configured access token.')
    logout_parser.add_argument('--host', help='Installation hostname or origin. Defaults to NIYAN_HOST or configured host.')
    logout_parser.add_argument('--local', action='store_true', help='Target the current checkout binding instead of user configuration.')
    logout_parser.add_argument('--dataset', help='Target an exact dataset-bound credential instead of the user-level binding.')
    logout_parser.add_argument('--forget', action='store_true', help='Remove local credential state without server revocation.')

    dataset_parser = commands.add_parser('dataset', help='Work with dataset repositories.')
    dataset_commands = dataset_parser.add_subparsers(dest='dataset_command', required=True)
    create_parser = dataset_commands.add_parser('create', help='Create an empty remote dataset.')
    create_parser.add_argument('dataset_path', nargs='?', help='Dataset path in namespace/dataset form. Prompts when omitted in a terminal.')
    create_parser.add_argument('--name', help='Dataset display name. Defaults to the slug.')
    create_parser.add_argument('--clone', action='store_true', help='Clone the empty dataset after creation.')
    create_parser.add_argument('--full-history', action='store_true', help='With --clone, fetch complete history instead of the shallow default.')
    create_parser.add_argument('--host', help='Installation hostname or origin. Defaults to NIYAN_HOST or configured host.')
    clone_parser = dataset_commands.add_parser('clone', help='Clone a dataset through Niyān-managed Git authentication.')
    clone_parser.add_argument('dataset_path', help='Dataset path in namespace/dataset form.')
    clone_parser.add_argument('destination', nargs='?', help='Checkout destination. Defaults to the dataset slug.')
    clone_parser.add_argument('--full-history', action='store_true', help='Fetch complete history and remote branches instead of the shallow default.')
    clone_parser.add_argument('--include', action='append', metavar='GLOB', help='Materialize only matching Git LFS paths. Repeat for multiple globs.')
    clone_parser.add_argument('--exclude', action='append', metavar='GLOB', help='Do not materialize matching Git LFS paths. Repeat for multiple globs.')
    clone_parser.add_argument('--metadata-only', action='store_true', help='Clone Git metadata and pointer files without downloading Git LFS content.')
    clone_parser.add_argument('--host', help='Installation hostname or origin. Defaults to NIYAN_HOST or configured host.')
    list_parser = dataset_commands.add_parser('list', help='List datasets visible to the current user.')
    list_parser.add_argument('namespace', nargs='?', help='Optional root or nested namespace path.')
    list_parser.add_argument('--limit', type=int, default=100, choices=range(1, 101), metavar='1..100', help='Maximum datasets to return. Defaults to 100.')
    list_parser.add_argument('--host', help='Installation hostname or origin. Defaults to NIYAN_HOST or configured host.')
    view_parser = dataset_commands.add_parser('view', help='Show remote dataset metadata and README content.')
    view_parser.add_argument('dataset_path', nargs='?', help='Dataset path in namespace/dataset form. Prompts when omitted in a terminal.')
    view_parser.add_argument('--web', action='store_true', help='Open the dataset in the web application.')
    view_parser.add_argument('--host', help='Installation hostname or origin. Defaults to NIYAN_HOST or configured host.')
    edit_parser = dataset_commands.add_parser('edit', help='Change a remote dataset slug or display name.')
    edit_parser.add_argument('dataset_path', nargs='?', help='Dataset path in namespace/dataset form. Prompts when omitted in a terminal.')
    edit_parser.add_argument('--slug', help='Replacement dataset path slug.')
    edit_parser.add_argument('--name', help='Replacement dataset display name.')
    edit_parser.add_argument('--host', help='Installation hostname or origin. Defaults to NIYAN_HOST or configured host.')
    delete_parser = dataset_commands.add_parser('delete', help='Permanently delete a remote dataset and its repository.')
    delete_parser.add_argument('dataset_path', nargs='?', help='Dataset path in namespace/dataset form. Prompts when omitted in a terminal.')
    delete_parser.add_argument('--confirm', help='Exact dataset path required for non-interactive deletion.')
    delete_parser.add_argument('--host', help='Installation hostname or origin. Defaults to NIYAN_HOST or configured host.')

    tree_parser = dataset_commands.add_parser('tree', help='List a remote dataset directory without a checkout.')
    tree_parser.add_argument('dataset_path', help='Dataset path in namespace/dataset form.')
    tree_parser.add_argument('repository_path', nargs='?', default='', help='Optional repository directory path.')
    tree_parser.add_argument('--ref', dest='revision', help='Branch, tag, or full commit. Defaults to the dataset default branch.')
    tree_parser.add_argument('--host', help='Installation hostname or origin. Defaults to NIYAN_HOST or configured host.')
    cat_parser = dataset_commands.add_parser('cat', help='Stream one remote dataset file to standard output.')
    cat_parser.add_argument('dataset_path', help='Dataset path in namespace/dataset form.')
    cat_parser.add_argument('repository_path', help='Repository file path.')
    cat_parser.add_argument('--ref', dest='revision', help='Branch, tag, or full commit. Defaults to the dataset default branch.')
    cat_parser.add_argument('--host', help='Installation hostname or origin. Defaults to NIYAN_HOST or configured host.')
    download_parser = dataset_commands.add_parser('download', help='Download remote dataset files or directories without a checkout.')
    download_parser.add_argument('dataset_path', help='Dataset path in namespace/dataset form.')
    download_parser.add_argument('repository_paths', nargs='*', help='Selected repository files or directories. Defaults to the complete dataset tree.')
    download_parser.add_argument('--ref', dest='revision', help='Branch, tag, or full commit. Defaults to the dataset default branch.')
    download_parser.add_argument('--output', dest='output_directory', help='Destination directory. Defaults to a dataset-named directory in the current working directory.')
    download_parser.add_argument('--host', help='Installation hostname or origin. Defaults to NIYAN_HOST or configured host.')

    dependency_add_parser = dataset_commands.add_parser('add', help='Add a dataset to the current project as a standard Git submodule.')
    dependency_add_parser.add_argument('dataset_path', help='Dataset path in namespace/dataset form.')
    dependency_add_parser.add_argument('path', nargs='?', help='Project-relative submodule path. Defaults to the dataset slug.')
    dependency_add_parser.add_argument('--ref', dest='revision', help='Branch, tag, or full commit. Defaults to the dataset default branch.')
    dependency_add_parser.add_argument('--host', help='Installation hostname or origin. Defaults to NIYAN_HOST or configured host.')
    dependency_remove_parser = dataset_commands.add_parser('remove', help='Remove and stage one dataset submodule dependency.')
    dependency_remove_parser.add_argument('path', help='Project-relative submodule path.')
    dependency_update_parser = dataset_commands.add_parser('update', help='Update one or all dataset submodule dependencies.')
    dependency_update_parser.add_argument('path', nargs='?', help='Optional project-relative submodule path. Defaults to all moving dependencies.')
    dependency_update_parser.add_argument('--ref', dest='revision', help='Branch, tag, or full commit to pin explicitly.')
    dataset_commands.add_parser('status', help='Show dataset submodule dependencies in the current project.')

    access_parser = dataset_commands.add_parser('access', help='Manage explicit dataset access grants.')
    access_commands = access_parser.add_subparsers(dest='access_command', required=True)
    access_list_parser = access_commands.add_parser('list', help='List explicit user and group grants.')
    _add_access_target_arguments(access_list_parser)
    access_list_parser.add_argument('--json', action='store_true', help='Write stable JSON to standard output.')
    for command_name, command_help in (('grant', 'Create an explicit dataset grant.'), ('edit', 'Change an explicit dataset grant.'), ('revoke', 'Revoke an explicit dataset grant.')):
        command_parser = access_commands.add_parser(command_name, help=command_help)
        _add_access_target_arguments(command_parser)
        principal = command_parser.add_mutually_exclusive_group(required=True)
        principal.add_argument('--user', metavar='USERNAME', help='Exact existing username.')
        principal.add_argument('--group', metavar='GROUP_PATH', help='Visible Niyān group path.')
        if command_name != 'revoke':
            command_parser.add_argument('--role', required=True, choices=('reader', 'contributor', 'maintainer', 'owner'))
        command_parser.add_argument('--json', action='store_true', help='Write stable JSON to standard output.')

    commands.add_parser('status', help='Show dataset working-copy and LFS state.')
    add_parser = commands.add_parser('add', help='Stage dataset paths using Niyān’s Git LFS tracking policy.')
    add_parser.add_argument('paths', nargs='*', help='One or more Git pathspecs to stage.')
    add_parser.add_argument('--all', action='store_true', help='Stage every working-tree change.')
    add_mode = add_parser.add_mutually_exclusive_group()
    add_mode.add_argument('--lfs', action='store_true', help='Persist Git LFS tracking for selected regular files before staging.')
    add_mode.add_argument('--git', action='store_true', help='Persist ordinary Git tracking for selected regular files before staging.')
    restore_parser = commands.add_parser('restore', help='Restore dataset paths from the index or unstage them.')
    restore_parser.add_argument('paths', nargs='+', help='One or more paths to restore.')
    restore_parser.add_argument('--staged', action='store_true', help='Unstage paths while preserving their working-tree content.')
    commit_parser = commands.add_parser('commit', help='Create a local dataset commit from staged changes.')
    commit_parser.add_argument('-m', '--message', help='Commit message. Git opens its configured editor when omitted.')
    diff_parser = commands.add_parser('diff', help='Show unstaged or staged dataset changes.')
    diff_parser.add_argument('--staged', action='store_true', help='Compare the index to HEAD instead of the working tree to the index.')
    log_parser = commands.add_parser('log', help='Show bounded dataset commit history.')
    log_parser.add_argument('--limit', type=_positive_integer, default=20, help='Maximum commits to show. Defaults to 20.')

    branch_parser = commands.add_parser('branch', help='Manage local dataset branches.')
    branch_commands = branch_parser.add_subparsers(dest='branch_command', required=True)
    branch_commands.add_parser('list', help='List local branches and upstreams.')
    branch_create_parser = branch_commands.add_parser('create', help='Create a local branch without switching.')
    branch_create_parser.add_argument('name')
    branch_create_parser.add_argument('start_point', nargs='?')
    branch_rename_parser = branch_commands.add_parser('rename', help='Rename a local branch.')
    branch_rename_parser.add_argument('old_name')
    branch_rename_parser.add_argument('new_name')
    branch_delete_parser = branch_commands.add_parser('delete', help='Safely delete a merged local branch.')
    branch_delete_parser.add_argument('name')

    switch_parser = commands.add_parser('switch', help='Switch local dataset branches.')
    switch_parser.add_argument('--create', action='store_true', help='Create the branch before switching to it.')
    switch_parser.add_argument('branch')
    switch_parser.add_argument('start_point', nargs='?')
    merge_parser = commands.add_parser('merge', help='Merge a branch or commit using ordinary Git semantics.')
    merge_parser.add_argument('revision')

    tag_parser = commands.add_parser('tag', help='Manage local annotated dataset tags.')
    tag_commands = tag_parser.add_subparsers(dest='tag_command', required=True)
    tag_commands.add_parser('list', help='List local tags.')
    tag_create_parser = tag_commands.add_parser('create', help='Create an annotated tag at HEAD.')
    tag_create_parser.add_argument('name')
    tag_create_parser.add_argument('-m', '--message', help='Annotation message. Git opens its configured editor when omitted.')
    tag_delete_parser = tag_commands.add_parser('delete', help='Delete a local tag.')
    tag_delete_parser.add_argument('name')

    commands.add_parser('fetch', help='Fetch remote Git refs without modifying the working tree or downloading LFS objects.')
    pull_parser = commands.add_parser('pull', help='Fast-forward the current branch and materialize its configured LFS working set.')
    pull_parser.add_argument('--full-history', action='store_true', help='Convert a shallow checkout to complete Git history.')
    pull_parser.add_argument('--include', action='append', metavar='GLOB', help='Replace the saved Git LFS include selection. Repeat for multiple globs.')
    pull_parser.add_argument('--exclude', action='append', metavar='GLOB', help='Replace the saved Git LFS exclude selection. Repeat for multiple globs.')
    pull_parser.add_argument('--metadata-only', action='store_true', help='Update Git metadata and pointer files without downloading Git LFS content.')
    commands.add_parser('push', help='Upload required Git LFS objects and publish the current dataset branch.')

    cache_parser = commands.add_parser('cache', help='Inspect and safely reclaim local Git LFS storage.')
    cache_commands = cache_parser.add_subparsers(dest='cache_command', required=True)
    cache_commands.add_parser('status', help='Show total, protected, and reclaimable local Git LFS content.')
    cache_prune_parser = cache_commands.add_parser('prune', help='Verify and permanently remove reclaimable local Git LFS content.')
    cache_prune_parser.add_argument('--dry-run', action='store_true', help='Report verified candidates without deleting any local objects.')

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
    internal_transfer = bool(command_line and command_line[0] == '_lfs-transfer')
    if internal_helper:
        parser = build_credential_helper_parser()
    elif internal_transfer:
        parser = argparse.ArgumentParser(prog='niyan _lfs-transfer')
    else:
        parser = build_parser()
    arguments = parser.parse_args(command_line[1:] if internal_helper or internal_transfer else command_line)
    paths = AppPaths.from_environment()
    stores = CredentialStores(paths=paths)
    try:
        if internal_transfer:
            return run_lfs_transfer(paths=paths, stores=stores, cwd=Path.cwd())
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
            if arguments.with_token:
                if arguments.read_only or arguments.no_browser:
                    parser.error('--with-token cannot be combined with --read-only or --no-browser because the token already has server-defined scopes.')
                login_with_token(
                    host=host,
                    raw_token=_read_access_token(),
                    paths=paths,
                    stores=stores,
                    local=arguments.local,
                    dataset_path=arguments.dataset,
                    insecure_storage=arguments.insecure_storage,
                    cwd=Path.cwd(),
                )
            else:
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
        if arguments.command == 'auth' and arguments.auth_command == 'status':
            host = resolve_host(arguments.host, paths=paths, cwd=Path.cwd())
            accepted = authentication_status(host=host, paths=paths, stores=stores, dataset_path=arguments.dataset, cwd=Path.cwd())
            return 0 if accepted else 3
        if arguments.command == 'auth' and arguments.auth_command == 'logout':
            host = resolve_host(arguments.host, paths=paths, cwd=Path.cwd())
            logout(
                host=host,
                paths=paths,
                stores=stores,
                local=arguments.local,
                dataset_path=arguments.dataset,
                forget=arguments.forget,
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
                full_history=arguments.full_history,
                include=arguments.include,
                exclude=arguments.exclude,
                metadata_only=arguments.metadata_only,
                cwd=Path.cwd(),
            )
            return 0
        if arguments.command == 'dataset' and arguments.dataset_command == 'create':
            if arguments.full_history and not arguments.clone:
                parser.error('--full-history requires --clone when creating a dataset.')
            host = resolve_host(arguments.host, paths=paths, cwd=Path.cwd())
            create_remote_dataset(
                host=host,
                dataset_path=arguments.dataset_path,
                name=arguments.name,
                clone=arguments.clone,
                full_history=arguments.full_history,
                paths=paths,
                stores=stores,
                cwd=Path.cwd(),
            )
            return 0
        if arguments.command == 'dataset' and arguments.dataset_command == 'list':
            host = resolve_host(arguments.host, paths=paths, cwd=Path.cwd())
            list_remote_datasets(host=host, namespace_path=arguments.namespace, limit=arguments.limit, paths=paths, stores=stores, cwd=Path.cwd())
            return 0
        if arguments.command == 'dataset' and arguments.dataset_command == 'view':
            host = resolve_host(arguments.host, paths=paths, cwd=Path.cwd())
            view_remote_dataset(host=host, dataset_path=arguments.dataset_path, web=arguments.web, paths=paths, stores=stores, cwd=Path.cwd())
            return 0
        if arguments.command == 'dataset' and arguments.dataset_command == 'edit':
            host = resolve_host(arguments.host, paths=paths, cwd=Path.cwd())
            edit_remote_dataset(host=host, dataset_path=arguments.dataset_path, slug=arguments.slug, name=arguments.name, paths=paths, stores=stores, cwd=Path.cwd())
            return 0
        if arguments.command == 'dataset' and arguments.dataset_command == 'delete':
            host = resolve_host(arguments.host, paths=paths, cwd=Path.cwd())
            delete_remote_dataset(host=host, dataset_path=arguments.dataset_path, confirmation=arguments.confirm, paths=paths, stores=stores, cwd=Path.cwd())
            return 0
        if arguments.command == 'dataset' and arguments.dataset_command == 'tree':
            host = resolve_host(arguments.host, paths=paths, cwd=Path.cwd())
            show_remote_tree(host=host, dataset_path=arguments.dataset_path, repository_path=arguments.repository_path, revision=arguments.revision, paths=paths, stores=stores, cwd=Path.cwd())
            return 0
        if arguments.command == 'dataset' and arguments.dataset_command == 'cat':
            host = resolve_host(arguments.host, paths=paths, cwd=Path.cwd())
            stream_remote_file(host=host, dataset_path=arguments.dataset_path, repository_path=arguments.repository_path, revision=arguments.revision, paths=paths, stores=stores, cwd=Path.cwd())
            return 0
        if arguments.command == 'dataset' and arguments.dataset_command == 'download':
            host = resolve_host(arguments.host, paths=paths, cwd=Path.cwd())
            download_remote_paths(host=host, dataset_path=arguments.dataset_path, repository_paths=arguments.repository_paths, revision=arguments.revision, output_directory=arguments.output_directory, paths=paths, stores=stores, cwd=Path.cwd())
            return 0
        if arguments.command == 'dataset' and arguments.dataset_command == 'add':
            host = resolve_host(arguments.host, paths=paths, cwd=Path.cwd())
            add_dataset_dependency(host=host, dataset_path=arguments.dataset_path, destination=arguments.path, revision=arguments.revision, paths=paths, stores=stores, cwd=Path.cwd())
            return 0
        if arguments.command == 'dataset' and arguments.dataset_command == 'remove':
            remove_dataset_dependency(dependency_path=arguments.path, cwd=Path.cwd())
            return 0
        if arguments.command == 'dataset' and arguments.dataset_command == 'update':
            update_dataset_dependencies(dependency_path=arguments.path, revision=arguments.revision, paths=paths, stores=stores, cwd=Path.cwd())
            return 0
        if arguments.command == 'dataset' and arguments.dataset_command == 'status':
            show_dataset_dependencies(cwd=Path.cwd())
            return 0
        if arguments.command == 'dataset' and arguments.dataset_command == 'access':
            host = resolve_host(arguments.host, paths=paths, cwd=Path.cwd())
            common = {'host': host, 'dataset_path': arguments.dataset_path, 'paths': paths, 'stores': stores, 'json_output': arguments.json, 'cwd': Path.cwd()}
            if arguments.access_command == 'list':
                list_dataset_access(**common)
            else:
                principal_type = 'user' if arguments.user is not None else 'group'
                principal = arguments.user if arguments.user is not None else arguments.group
                if arguments.access_command == 'grant':
                    grant_dataset_access(principal_type=principal_type, principal=principal, role=arguments.role, **common)
                elif arguments.access_command == 'edit':
                    edit_dataset_access(principal_type=principal_type, principal=principal, role=arguments.role, **common)
                else:
                    revoke_dataset_access(principal_type=principal_type, principal=principal, **common)
            return 0
        if arguments.command == 'status':
            show_status(cwd=Path.cwd())
            return 0
        if arguments.command == 'add':
            if arguments.all and arguments.paths:
                parser.error('--all cannot be combined with explicit paths.')
            if not arguments.all and not arguments.paths:
                parser.error('add requires at least one path or --all.')
            stage_paths(arguments.paths, all_paths=arguments.all, force_lfs=arguments.lfs, force_git=arguments.git, cwd=Path.cwd())
            return 0
        if arguments.command == 'restore':
            restore_paths(arguments.paths, staged=arguments.staged, cwd=Path.cwd())
            return 0
        if arguments.command == 'commit':
            commit_changes(message=arguments.message, cwd=Path.cwd())
            return 0
        if arguments.command == 'diff':
            show_diff(staged=arguments.staged, cwd=Path.cwd())
            return 0
        if arguments.command == 'log':
            show_log(limit=arguments.limit, cwd=Path.cwd())
            return 0
        if arguments.command == 'branch':
            if arguments.branch_command == 'list':
                list_branches(cwd=Path.cwd())
            elif arguments.branch_command == 'create':
                create_branch(arguments.name, start_point=arguments.start_point, cwd=Path.cwd())
            elif arguments.branch_command == 'rename':
                rename_branch(arguments.old_name, arguments.new_name, cwd=Path.cwd())
            else:
                delete_branch(arguments.name, cwd=Path.cwd())
            return 0
        if arguments.command == 'switch':
            if arguments.start_point is not None and not arguments.create:
                parser.error('a start point requires --create')
            switch_branch(arguments.branch, create=arguments.create, start_point=arguments.start_point, cwd=Path.cwd())
            return 0
        if arguments.command == 'merge':
            merge_revision(arguments.revision, cwd=Path.cwd())
            return 0
        if arguments.command == 'tag':
            if arguments.tag_command == 'list':
                list_tags(cwd=Path.cwd())
            elif arguments.tag_command == 'create':
                create_tag(arguments.name, message=arguments.message, cwd=Path.cwd())
            else:
                delete_tag(arguments.name, cwd=Path.cwd())
            return 0
        if arguments.command == 'fetch':
            fetch_dataset(paths=paths, stores=stores, cwd=Path.cwd())
            return 0
        if arguments.command == 'pull':
            pull_dataset(
                paths=paths,
                stores=stores,
                full_history=arguments.full_history,
                include=arguments.include,
                exclude=arguments.exclude,
                metadata_only=arguments.metadata_only,
                cwd=Path.cwd(),
            )
            return 0
        if arguments.command == 'push':
            push_dataset(paths=paths, stores=stores, cwd=Path.cwd())
            return 0
        if arguments.command == 'cache' and arguments.cache_command == 'status':
            show_cache_status(paths=paths, stores=stores, cwd=Path.cwd())
            return 0
        if arguments.command == 'cache' and arguments.cache_command == 'prune':
            prune_cache(paths=paths, stores=stores, dry_run=arguments.dry_run, cwd=Path.cwd())
            return 0
        parser.error('Unsupported command.')
    except NiyanCliError as error:
        print(f'error: {error}', file=sys.stderr)
        return _error_exit_status(error)


def _read_access_token(*, stdin=None, secret_prompt=getpass.getpass):
    """Read one access token without accepting it as a command argument.

    Parameters
    ----------
    stdin : file-like object, optional
        Standard input override used by tests.
    secret_prompt : callable, optional
        Non-echoing terminal prompt.

    Returns
    -------
    str
        Unvalidated token text for server-backed login validation.
    """

    stream = stdin or sys.stdin
    try:
        interactive = stream.isatty()
    except (AttributeError, OSError):
        interactive = False
    value = secret_prompt('Access token: ') if interactive else stream.read(4097)
    if len(value) > 4096:
        raise CredentialError('The supplied access token is too long.')
    return value


def _error_exit_status(error):
    """Map public CLI failures to the documented exit categories."""

    if isinstance(error, CredentialError):
        return 3
    if isinstance(error, ApiError):
        if error.status in (400, 422):
            return 2
        if error.status == 401:
            return 3
        if error.status == 403:
            return 4
        if error.status == 404:
            return 5
        if error.status == 409:
            return 6
        if error.status in (429, 500, 502, 503, 504):
            return 7
        if error.status is None:
            return 7
    if isinstance(error, ConfigurationError):
        return 2
    if isinstance(error, GitConflictError):
        return 6
    if isinstance(error, GitDependencyError):
        return 8
    return 1


def _positive_integer(value):
    """Parse one strictly positive command-line integer."""

    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError('value must be a positive integer')
    return parsed


if __name__ == '__main__':
    raise SystemExit(main())
