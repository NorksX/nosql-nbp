"""Simplest possible test: put some movies in, get one back, scan them."""

import json

import fdb

fdb.api_version(730)
db = fdb.open("/etc/foundationdb/fdb.cluster")

print("1. load 1000 movies")
loaded = 0
batch = []
with open("/work/data/tmdb-movies-2008.json") as fh:
    for line in fh:
        if loaded >= 1000:
            break
        m = json.loads(line)
        date = m.get("release_date") or ""
        key = b"movie:" + str(m["id"]).encode()
        value = json.dumps(
            {
                "id": m["id"],
                "title": m.get("title") or "",
                "year": int(date[:4]) if date[:4].isdigit() else 0,
                "language": m.get("original_language") or "",
                "rating": float(m.get("vote_average") or 0),
                "votes": int(m.get("vote_count") or 0),
            }
        ).encode()
        batch.append((key, value))
        loaded += 1


@fdb.transactional
def write_batch(tr, pairs):
    for key, value in pairs:
        tr[key] = value


# write in batches of 500 (FoundationDB limits how much one transaction can do)
for i in range(0, len(batch), 500):
    write_batch(db, batch[i : i + 500])
print(f"   loaded {loaded}")

print("2. get one movie back by its key")
some_id = json.loads(batch[0][1])["id"]
print(f"   movie:{some_id} ->", json.loads(db[b'movie:' + str(some_id).encode()])["title"])


@fdb.transactional
def scan_all(tr):
    return [json.loads(v) for _, v in tr[b"movie:" : b"movie;"]]


print("3. query: English movies from 2008 rated 7+ with at least 100 votes")
movies = scan_all(db)
hits = [
    m
    for m in movies
    if m["year"] == 2008 and m["language"] == "en" and m["rating"] >= 7 and m["votes"] >= 100
]
for m in sorted(hits, key=lambda m: -m["rating"]):
    print(f"   {m['rating']:>4}  ({m['votes']:>5} votes)  {m['title']}")
print(f"\n   {len(hits)} rows (scanned {len(movies)})")
