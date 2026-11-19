# Repository notes: cloudflared-tunnel-manager

These notes are repo-specific. Global agent instructions (tool use, editing,
verification, communication) still apply.

## What this is

A small homelab control plane that keeps Cloudflare DNS records and a
Cloudflare Tunnel ingress config in sync with labelled Docker containers.

- Roughly 770 lines of Python across three modules plus one offline self-check.
- Single-threaded, synchronous. Runs as a Docker image alongside `cloudflared`.
- No linter config and no type checking.
- Dependencies are pinned in `tunnel-manager/requirements.txt`.

Keep changes proportionate to a project this size. A new framework, a caching
layer, an async rewrite, or a test harness beyond the existing self-check is
almost never the smallest change that solves a problem here.

## How it converges

Two paths, deliberately redundant:

1. **Event path.** A Docker `start`/`die` event reaches
   `DockerManager.watch_events` -> `handle_container_event` ->
   `CloudflareManager.handle_container_update`, which applies the DNS and
   ingress change and pushes the tunnel config only if the ingress cache
   actually changed.
2. **Reconcile path.** `watch_events` deliberately returns every 60 seconds.
   `main` then calls `reconcile`, which rebuilds desired state from the running
   containers, re-reads Cloudflare state, and applies the minimal diff.

The reconcile pass is what repairs state after missed events, failed API calls,
or downtime. The event path is an optimisation for latency, not the source of
truth. Do not turn the event path back into the only mechanism.

## The cost model

This is I/O-bound. "Performance" here means API round trips, not CPU.

- **Idle baseline:** two reads per 60 seconds (`dns.records.list`,
  `tunnels.configurations.get`). Zero writes.
- **Per event:** zero or one DNS write, plus one tunnel config push only when
  the ingress cache changed.
- **Startup:** one reconcile, so roughly `2 + N` calls for N labelled
  containers.

Anything that reintroduces per-event reads or unconditional pushes is a
regression. The mutators return `bool` precisely so `reconcile` and
`handle_container_update` can skip no-op writes; keep that contract.

Still single-threaded: the SDK defaults to a 60s read timeout, 5s connect, and
2 retries. One slow call stalls every queued event for up to about a minute.
Do not add unbounded work to the event path. CPU profiling and load tests are
the wrong instrument here.

## Ownership rules

Pruning is the only destructive operation, so ownership decides what may be
deleted.

- **DNS:** a record is ours only when its `comment` is in `MANAGED_COMMENTS`.
  Anything else is left alone. Legacy records written by an earlier scratch
  script carry `updated via dns-manager` and are still prunable.
- **Ingress:** `ConfigIngress` has no comment field, so ownership is inferred in
  `_is_managed_rule`: hostname under `DOMAIN` and service starting with
  `http://{HOST_IP}:`. This is marked as a `shortcut:` in the code.

Consequence worth remembering: changing `HOST_IP` orphans every existing
ingress rule, because they stop matching the service prefix and will never be
pruned.

Pruning is skipped on the first reconcile of a process (`_has_reconciled`).
On a host reboot the sibling containers may not have started yet, and pruning
then would delete every managed record. Do not remove that guard without
replacing it with something equally safe.

## Gotchas specific to this stack

- **The SDK is alias-first.** `ConfigIngress(origin_request=...)` silently
  lands in `extra` and leaves the real field `None`, because the field alias is
  `originRequest`. Same for `keepAliveTimeout` and friends. Construct models
  with aliases, or verify the field actually took the value.
- **`insert(-1)` before the catch-all assumes a catch-all rule exists.** That
  assumption predates this code; it is not handled.
- **`docker` 6.x is incompatible with `urllib3` 2.x.** A fresh build resolves
  `urllib3` 2.6+, which breaks the `http+docker` connection scheme with
  "Not supported URL scheme http+docker". Keep `docker>=7`. Downgrading it
  reintroduces the failure.
- **No optimistic concurrency.** The tunnel config update accepts only
  `account_id` and `config` in SDK 4.0.0. There is no version or ETag to pass.
  The `version` field on the response is a read-only hint.
- **`accepted_methods.py` is dead scratch code.** It is excluded from the image
  by `.dockerignore`. Read it for API examples if useful; do not wire it in.

## Verifying changes

- `python3 -m py_compile tunnel-manager/*.py` catches syntax errors only.
- `python3 tunnel-manager/test_reconcile.py` exercises `reconcile` against fake
  clients. It needs the `cloudflare` package, so run it in a local venv, or mount
  it into the built image since `.dockerignore` keeps it out of the image:
  `docker run --rm -v "$PWD/tunnel-manager:/mnt:ro" cloudflared-tunnel-manager:pr python /mnt/test_reconcile.py`.
  It makes no network calls.
- Behaviour changes that the self-check does not cover need a live check, which
  means touching a real Cloudflare account. Ask before doing that.

## Guardrails for suggestions and changes

- Smallest change that fully solves the problem. This repo has no appetite for
  speculative abstraction.
- Reuse the existing `CloudflareManager` and `DockerManager`. Do not introduce a
  third manager or a service layer at this size.
- Do not add dependencies without a concrete reason. The `cloudflare` and
  `docker` SDKs already handle retries, pagination, and connection management.
- Preserve the public contract: compose env var names, the read-only docker
  socket mount, the `cloudflare.*` label scheme, and the log path in
  `/app/logs`.
- Treat the `while True` restart wrapper, the `watch_events` timeout, and the
  reconcile pass as one mechanism. Changing one usually means revisiting the
  others.
- With no broad test suite, state what was actually verified (command + result)
  and what remains unverified. Do not claim success from inspection alone.

## Open items

These are known and deliberately not fixed:

- The event stream has no `since` cursor. Reconcile covers the gap, so a missed
  event costs at most one reconcile interval.
- `record.name.endswith(self.domain)` in `get_dns_records` is redundant: the
  query is already scoped to `zone_id`.
- A burst of container starts still pushes the tunnel config once per event.
  Debouncing would need either a thread or a signal-safe flush and is not worth
  it at homelab scale.
