# Nightly and post-merge release promotion

`.github/workflows/nightly.yml` turns a verified `main` into a Windows prerelease
without anyone pushing `release-candidate` by hand. It never builds anything
itself: it fast-forwards `release-candidate` and dispatches the existing Windows
desktop workflow (`.github/workflows/desktop.yml`) on that branch, which builds,
tests, packages and publishes the prerelease exactly as a manual push would.

## When it promotes

Two entries run the same `Promote` step:

| Entry | Trigger | Commit promoted | Waits for verification |
| --- | --- | --- | --- |
| Nightly | `schedule` at 19:00 UTC (03:00 Beijing), or `workflow_dispatch` | `main` as it is at that moment | No |
| Post-merge | a pull request labelled `release` merges into `main` | that pull request's merge commit | Yes, polling every 60 s for up to 40 min |

The step promotes only when all of these hold:

1. The commit differs from `release-candidate` and is not already contained in it.
   Otherwise it writes "No change since the last release" to the run summary and
   stops green.
2. `release-candidate` is an ancestor of the commit, so the push is a
   fast-forward. A diverged `release-candidate` fails the run with a clear message;
   reconcile the branch by hand.
3. The latest `ArchFlow Verify` run of that exact commit on `main` concluded
   `success`. A missing, still-running or failed run stops without promoting and
   says why in the summary. The nightly run then stays green; the post-merge run
   fails, because someone asked for that release explicitly.

It then pushes `<commit>:refs/heads/release-candidate` without force and runs
`gh workflow run desktop.yml --ref release-candidate`. A push made with the
workflow's `GITHUB_TOKEN` starts no other workflow; a `workflow_dispatch` does.
`desktop.yml` treats a dispatch on `release-candidate` exactly like a push there:
the release version is still `0.1.<run number>`, and the `publish` job still holds
the only writable token.

Promotions share the `nightly-release` concurrency group and are never cancelled
halfway. The post-merge entry runs on the `pull_request` event, whose token is
read-only for a pull request from a fork; such a merge is picked up by the next
nightly run instead.

## Dry run

Actions, **Nightly release promotion**, **Run workflow** on `main`, tick
`dry_run`. The run performs every check and writes what it would promote, but
pushes nothing and dispatches nothing. `workflow_dispatch` only exists once the
workflow file is on the default branch.

## How the Hub picks it up

Nothing changes in the Hub. An installed MonkeyHub on the unsigned prerelease
channel checks the public releases 30 s after start and then every 6 h
(`apps/monkeyhub/api/monkeyhub_api/updates/`), downloads and prepares the delta
patch for its exact installed commit, and switches to the new version when the
application next quits normally. It never restarts itself.

## Stopping it

Disable the workflow: Actions, **Nightly release promotion**, **...**, **Disable
workflow** (or `gh workflow disable nightly.yml`). That stops both the nightly and
the post-merge entries; a manual push to `release-candidate` still releases as
before. To skip only the post-merge path, do not apply the `release` label.
