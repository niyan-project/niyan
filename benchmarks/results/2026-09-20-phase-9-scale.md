# Phase 9 scale qualification — 2026-09-20

This run qualifies Niyān v1 at 100,000 and one million Git paths and verifies real ranged access to a multi-gigabyte Git LFS object. It does not promise support for ten million paths.

## Environments

The Git path-count cases ran on a disposable VPS with four AMD EPYC vCPUs, 8,321,531,904 bytes of RAM, 190 GiB of free persistent storage, Linux 7.0.0-31, and Git 2.53.0. Repositories contained 1,000 files per directory and reused one two-byte blob so the measurements isolate path-count costs from unique-content transfer costs.

The data-plane case ran the current Django and Python client code on an eight-core Apple Silicon workstation with disposable PostgreSQL 17 and Git storage. A remote private S3-compatible bucket held a 2,214,592,512-byte object assembled from 33 server-side copies of one 64 MiB seed part. This validates S3 multipart compatibility and bounded reads, not client upload bandwidth.

Raw measurements are preserved beside this report.

## Results

| Operation | 100,000 paths | 1,000,000 paths |
| --- | ---: | ---: |
| Generate and repack fixture | 1.00s | 33.23s |
| Recursively enumerate all paths | 0.058s | 0.321s |
| Niyān bounded tree pages, observed range | 0.008–0.021s | 0.011–0.022s |
| Shallow clone plus full checkout | 4.55s | 56.97s |
| Clean `git status` | 0.388s | 2.42s |
| Fetch one changed blob | 0.068s | 0.423s |
| Create one empty commit | 0.148s | 1.59s |
| Push one commit | 0.056s | 0.177s |
| Bare repository size | 440 KiB | 2.75 MiB |
| Checked-out working tree size | 404 MiB | 3.94 GiB |

The multi-gigabyte object was assembled by the provider in 52.21s. Five 100-object Git LFS Batch negotiations took 1.64–2.13s with a 1.77s median. Through the public fsspec client, reading the first 1 MiB took 0.99s and seeking to byte 2,213,543,936 to read the last 1 MiB took 0.55s. The complete 2.06 GiB object was never downloaded.

The initial LFS Batch run exposed one S3 adapter being constructed for every object. It produced a 44.52s median at the protocol's normal 100-object batch size. Reusing one immutable adapter per request reduced the median by approximately 25 times without weakening authorization, validation, or signed-action isolation; a regression test now fixes this behavior.

## Initial regression budgets

These ceilings detect material regressions on broadly comparable hardware; they are not latency or throughput guarantees for every installation.

- A bounded Niyān tree page at one million paths must complete within 250ms.
- Recursive Git tree enumeration at one million paths must complete within 2s.
- A shallow clone with a one-million-file checkout must complete within 120s, clean status within 10s, and one-commit fetch or push within 5s.
- The median warm 100-object LFS Batch negotiation must complete within 5s against local PostgreSQL.
- The first and distant 1 MiB fsspec range reads from a multi-gigabyte LFS object must each complete within 5s on the reference object-store connection and must not materialize the complete object.

Provider-side multipart assembly has no cross-provider time budget. Its successful completion, exact final size, and ranged readability are the portable acceptance criteria.

## Accepted v1 limitations

- A shallow clone limits history but does not reduce the inode or filesystem-block cost of the checked-out version. One million two-byte files occupied 3.94 GiB. Niyān's recommended CLI can choose subsets, but standard Git users must make their own checkout choices.
- One million paths is qualified, not described as cheap. Ten million paths remains explicitly outside the v1 support promise.
- The Git fixture reuses one blob and therefore does not measure large packs containing one million unique payloads.
- The S3 object uses repeated zero-filled parts and provider-side multipart copy. It verifies object-size handling, signed ranges, Git LFS identity, and fsspec seeking, but not compression behavior or end-user upload bandwidth.
- Git LFS Batch remains bounded to 100 objects per request. The benchmark uses that normal maximum rather than relaxing it.
- Object-store latency and bandwidth remain deployment-specific. The budgets above are regression ceilings for the reference setup, not an S3 service-level objective.
