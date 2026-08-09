# Installation log — FoundationDB

Verified working on **macOS / Apple Silicon (aarch64), Docker 29.6.1, 8 CPU, 7.75 GB RAM**, 2026-08-09.

## Image

| | |
|---|---|
| Server image | `foundationdb/foundationdb:7.3.79` |
| Python client | `foundationdb==7.3.79` (PyPI) — **must match the server tag exactly** |
| API version | `730` |
| Architectures | linux/amd64 **and** linux/arm64 — pulled natively, **no emulation** |
| Storage engine | `ssd-2` |

> FoundationDB refuses mismatched client/server versions. The image tag and the pip
> pin move together or not at all. Do **not** add `platform:` to the compose file.

## Install

```bash
cd docker/foundationdb
docker compose up -d --build

# MANDATORY, once per data volume — FDB does not self-initialize
docker exec fdb fdbcli --exec "configure new single ssd"
```

Without the `configure new` step:

```
WARNING: Long delay (Ctrl-C to interrupt)
The database is unavailable; type `status' for more information.
```

After it:

```
Database created
```

## Verification

### 1. Cluster status

```bash
docker exec fdb fdbcli --exec "status"
```

```
  Storage engine         - ssd-2
  Log engine             - ssd-2
  Coordinators           - 1
  FoundationDB processes - 1
  Zones / Machines       - 1 / 1
  Memory availability    - 6.6 GB per process
  Fault Tolerance        - 0 machines
  Operating space        - 430.8 GB free
```

Immediately after `configure new`, `status minimal` reports *"available, but has
issues"* and Data shows `(Re)initializing automatic data distribution`. This is
**transient** initialization, not a fault; it clears on its own. `Fault Tolerance -
0 machines` is expected and correct for a `single` redundancy dev cluster.

### 2. Client library and round trip

```bash
docker compose exec client python docker/foundationdb/smoke_test.py
```

```
cluster file: /etc/foundationdb/fdb.cluster
   contents: docker:docker@172.19.0.2:4500
1. simple set/get
   -> smoke = ok
2. tuple layer + batched transaction
3. ordered range read
   -> ('movie', 0) ('title-0', 2000)
   -> ('movie', 1) ('title-1', 2001)
   -> ('movie', 2) ('title-2', 2002)
   -> ('movie', 3) ('title-3', 2003)
   -> ('movie', 4) ('title-4', 2004)
4. cleanup

SMOKE TEST PASSED
```

Step 3 confirms the property the Phase 2 index design depends on: **the keyspace is
ordered**, so tuple-packed keys come back in sorted order and range scans work.

### 3. Persistence

| Test | Result |
|---|---|
| `docker compose restart fdb` | key survived; available again in ~1 s |
| `docker compose down` + `up -d` | key survived; available in ~2 s; **no re-`configure new` needed** |

The cluster file was regenerated identically (`docker:docker@172.19.0.2:4500`) both times.

## Problems encountered and design decisions

### The cluster file is the whole problem

FoundationDB clients do not connect to a hostname you hand them — they read a *cluster
file* containing the address the server advertises. With `FDB_NETWORKING_MODE=container`
that address is the container's internal IP (`172.19.0.2`). So a client on the **host**
connecting to `localhost:4500` fails even though the port is published, because the
cluster file points somewhere the host cannot reach.

The usual Linux workaround is `FDB_NETWORKING_MODE=host` with `network_mode: host`.
**That does not work on Docker Desktop for Mac**, where the daemon runs inside a VM and
there is no shared host network.

**Decision:** all database access happens from the `client` container on the compose
network. This is not a Mac-only concession — it is the single approach that is identical
on macOS and x64 Linux, and it has the side benefit of making Phase 4 fair, since both
databases are then driven from an equivalent containerized Python 3.11 client. Port 4500
stays published only for `fdbcli` diagnostics.

**Cluster file distribution:** rather than copying the file around by hand, both
containers mount the shared `fdbconf` volume at `/etc/foundationdb` and both set
`FDB_CLUSTER_FILE=/etc/foundationdb/fdb.cluster`. The server writes it on boot; the
client mounts it read-only and reads the very same file. Confirmed self-healing: after a
full `down`/`up` the server rewrote the file and the client reconnected with no manual
step.

### Client library sourcing

The Python binding is pure Python and loads `libfdb_c.so` through `ctypes`, so the
shared library must match the server's version *and* CPU architecture. `Dockerfile.client`
lifts it straight out of the official server image with a multi-stage `COPY`, which
guarantees both automatically on any teammate's machine:

```dockerfile
FROM foundationdb/foundationdb:7.3.79 AS fdbsrc
FROM python:3.11-slim
COPY --from=fdbsrc /usr/lib/libfdb_c.so /usr/lib/libfdb_c.so
COPY --from=fdbsrc /usr/bin/fdbcli     /usr/bin/fdbcli
```

Both paths were confirmed to exist in the 7.3.79 image (the build succeeds; a missing
source path is a hard build error).

## Limits that will shape Phase 2

| Limit | Value |
|---|---|
| Value size | **100 KB** |
| Key size | 10 KB |
| Transaction size | **10 MB** |
| Transaction duration | **5 seconds** |

The L2 "whole year in one key" model exceeds the value limit (a 2014 year-blob is
~4.7 MB) and must be chunked. Bulk loading must batch at roughly 500–1,000 pairs per
transaction with retry on `transaction_too_old`.

## Everyday commands

```bash
docker compose up -d                              # start
docker exec fdb fdbcli --exec "status"            # cluster status
docker compose exec client bash                   # shell with the binding, repo at /work
docker compose down                               # stop, keep data
docker compose down -v                            # WIPE — requires `configure new` again
```
