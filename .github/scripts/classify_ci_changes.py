from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class ChangeSet:
    server: bool = False
    python_client: bool = False
    web: bool = False
    container: bool = False

    @classmethod
    def everything(cls) -> ChangeSet:
        return cls(server=True, python_client=True, web=True, container=True)

    def github_outputs(self) -> str:
        return "\n".join(
            (
                f"server={str(self.server).lower()}",
                f"python_client={str(self.python_client).lower()}",
                f"web={str(self.web).lower()}",
                f"container={str(self.container).lower()}",
            )
        )


FULL_CI_PATHS = {
    ".github/workflows/ci.yml",
    ".github/scripts/classify_ci_changes.py",
    ".github/scripts/test_classify_ci_changes.py",
}


def classify_paths(paths: Iterable[str]) -> ChangeSet:
    normalized_paths = {
        stripped_path[2:] if stripped_path.startswith("./") else stripped_path
        for path in paths
        if (stripped_path := path.strip())
    }
    if normalized_paths & FULL_CI_PATHS:
        return ChangeSet.everything()

    server = any(path.startswith("apps/server/") for path in normalized_paths) or "deploy/compose.test.yml" in normalized_paths
    python_client = any(path.startswith("clients/python/") for path in normalized_paths)
    web = any(path.startswith("apps/web/") for path in normalized_paths)
    container = (
        server
        or web
        or any(path.startswith("deploy/") for path in normalized_paths)
        or bool(normalized_paths & {"Dockerfile", ".dockerignore"})
    )

    if "codecov.yml" in normalized_paths:
        server = True
        python_client = True

    return ChangeSet(server=server, python_client=python_client, web=web, container=container)


def changed_paths(base: str, head: str) -> list[str]:
    completed = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", base, head],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.splitlines()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Classify changed paths for Niyān CI jobs.")
    parser.add_argument("--event", required=True, choices=("pull_request", "push", "workflow_dispatch"))
    parser.add_argument("--before", default="")
    parser.add_argument("--base", default="")
    parser.add_argument("--head", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.event == "workflow_dispatch":
        print(ChangeSet.everything().github_outputs())
        return 0

    base = args.base if args.event == "pull_request" else args.before
    if not base or set(base) == {"0"}:
        print("Could not identify a diff base; running the complete CI suite.", file=sys.stderr)
        print(ChangeSet.everything().github_outputs())
        return 0

    try:
        paths = changed_paths(base, args.head)
    except subprocess.CalledProcessError as error:
        print(f"Could not calculate changed paths ({error}); running the complete CI suite.", file=sys.stderr)
        print(ChangeSet.everything().github_outputs())
        return 0

    print(classify_paths(paths).github_outputs())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
