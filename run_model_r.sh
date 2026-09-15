#!/usr/bin/env bash
# Measure the normalized relational model (R) and regenerate the figures.
#
#     bash run_model_r.sh
#
# Run it from the repository root, with Docker Desktop started. Everything it
# writes lands in bench/results/ and bench/plots/; it does not touch the
# елаборат.
#
# Roughly 5 minutes, most of it the concurrency sweep.

set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die() { printf '\n\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

running() { [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = "true" ]; }

say "0/8  Checking the containers"
docker info >/dev/null 2>&1 || die "Docker is not running — start Docker Desktop first."
running pg-client || die "pg-client is not running. Start it with:
    cd docker/postgres && docker compose up -d"
echo "     pg-client is up"

for c in kv-client fdb-client; do
    if running "$c"; then echo "     $c is up"; else
        echo "     $c is NOT running — the cross-database check in step 6 will skip it"
    fi
done

say "1/8  Applying the relational schema"
docker exec pg-client python -m common.apply_schema --model r

say "2/8  Loading and verifying model R  (also records the load time)"
docker exec pg-client python -m common.live_check --model r

say "3/8  Latency sweep"
docker exec pg-client python -m bench.harness --db postgres-r

say "4/8  Concurrency sweep  (1 / 4 / 16 clients)"
docker exec pg-client python -m bench.concurrency --db postgres-r

say "5/8  Capturing query plans"
docker exec pg-client python -m bench.explain --schema r > docs/postgres-plans-r.txt
echo "     wrote docs/postgres-plans-r.txt"

say "6/8  Checking every database still returns the same answers"
docker exec pg-client python -m bench.answers --db postgres-r
running kv-client  && docker exec kv-client  python -m bench.answers || true
running fdb-client && docker exec fdb-client python -m bench.answers || true
docker exec pg-client python -m bench.answers
python -m bench.answers --compare \
    || die "the databases disagree — do not trust the numbers above, that is the finding"

say "7/8  Tables"
python -m bench.report > bench/results/report-with-r.md
echo "     wrote bench/results/report-with-r.md"

say "8/8  Figures"
python -m bench.plots

cat <<'DONE'

Done. The four new figures are in bench/plots/ :

  fig12_model_r_vs_kv.png      Слика 12 — L1 vs L2 vs R, all on PostgreSQL
  fig13_best_model_per_db.png  Слика 13 — each engine at its best
  fig14_load_with_r.png        Слика 14 — load time, R as a third group
  fig15_concurrency_r.png      Слика 15 — throughput, PostgreSQL as R

Слика 7–11 are regenerated unchanged. The елаборат is untouched.
The table for the report is in bench/results/report-with-r.md.
DONE
