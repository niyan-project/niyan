# Niyān benchmarks

These opt-in harnesses qualify explicit v1 scale claims; they are not part of the ordinary correctness test suite. Record the source revision, machine profile, commands, raw JSON, and interpretation whenever results establish or change a regression budget.

`git_scale.py` builds synthetic Git repositories without first materializing every path, then measures recursive browsing, the production Niyān repository browser, a shallow clone and checkout, status, fetch, and push. Its working directory can contain one million checked-out files. Run it only on a disposable machine with ample storage and inode capacity:

```shell
python benchmarks/git_scale.py --paths 100000 1000000 --output results.json
```

The fixture deliberately reuses one tiny blob. It measures Git path-count costs rather than data-transfer throughput. Git LFS negotiation and multi-gigabyte ranged-read qualification are recorded separately because object-store network performance is deployment-specific.

`data_plane_scale.py` creates a real 2.0625 GiB Git LFS object beneath a UUID-scoped prefix in a disposable S3-compatible bucket. It uses provider-side multipart copies of one 64 MiB seed part, serves the current Django application against disposable PostgreSQL and Git storage, measures 100-object Git LFS Batch negotiation, and reads 1 MiB ranges from the beginning and end through the public fsspec client. It removes all remote objects and stops PostgreSQL on success or failure:

```shell
NIYAN_RUN_DATA_PLANE_BENCHMARK=1 apps/server/.venv/bin/python benchmarks/data_plane_scale.py --env-file apps/server/.env --output data-plane.json
```

The reported object-assembly time measures the configured provider's server-side multipart copy path, not client upload throughput. The range-read results qualify bounded behavior and end-to-end compatibility; they are not a general bandwidth promise.
