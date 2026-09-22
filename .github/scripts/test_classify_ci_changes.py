import unittest

from classify_ci_changes import ChangeSet, classify_paths


class ClassifyPathsTests(unittest.TestCase):
    def test_documentation_changes_run_no_application_jobs(self) -> None:
        self.assertEqual(classify_paths(["docs/users/getting-started.md"]), ChangeSet())

    def test_server_changes_run_server_and_container_jobs(self) -> None:
        self.assertEqual(classify_paths(["apps/server/project/settings.py"]), ChangeSet(server=True, container=True))

    def test_python_client_changes_run_only_client_jobs(self) -> None:
        self.assertEqual(classify_paths(["clients/python/src/niyan/cli.py"]), ChangeSet(python_client=True))

    def test_web_changes_run_web_and_container_jobs(self) -> None:
        self.assertEqual(classify_paths(["apps/web/app.vue"]), ChangeSet(web=True, container=True))

    def test_deployment_changes_run_only_container_jobs(self) -> None:
        self.assertEqual(classify_paths(["deploy/Caddyfile"]), ChangeSet(container=True))

    def test_test_database_compose_changes_also_run_server_jobs(self) -> None:
        self.assertEqual(classify_paths(["deploy/compose.test.yml"]), ChangeSet(server=True, container=True))

    def test_codecov_changes_run_both_coverage_jobs(self) -> None:
        self.assertEqual(classify_paths(["codecov.yml"]), ChangeSet(server=True, python_client=True))

    def test_ci_implementation_changes_run_everything(self) -> None:
        for path in (
            ".github/workflows/ci.yml",
            ".github/scripts/classify_ci_changes.py",
            ".github/scripts/test_classify_ci_changes.py",
        ):
            with self.subTest(path=path):
                self.assertEqual(classify_paths([path]), ChangeSet.everything())

    def test_mixed_changes_combine_required_jobs(self) -> None:
        self.assertEqual(
            classify_paths(["clients/python/pyproject.toml", "apps/web/package.json"]),
            ChangeSet(python_client=True, web=True, container=True),
        )

    def test_dot_slash_prefixes_are_normalized(self) -> None:
        self.assertEqual(classify_paths(["./Dockerfile"]), ChangeSet(container=True))

    def test_dotfile_names_are_preserved(self) -> None:
        self.assertEqual(classify_paths([".dockerignore"]), ChangeSet(container=True))


if __name__ == "__main__":
    unittest.main()
