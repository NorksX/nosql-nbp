"""Simplest possible test: make a table, put some movies in, query them."""

import json
import os

from borneo import NoSQLHandle, NoSQLHandleConfig, PutRequest, QueryRequest, TableRequest
from borneo.kv import StoreAccessTokenProvider

handle = NoSQLHandle(
    NoSQLHandleConfig(os.environ["NOSQL_ENDPOINT"]).set_authorization_provider(
        StoreAccessTokenProvider()
    )
)

print("1. create table")
handle.do_table_request(
    TableRequest().set_statement(
        """CREATE TABLE IF NOT EXISTS movies (
             id INTEGER,
             title STRING,
             year INTEGER,
             language STRING,
             rating DOUBLE,
             votes INTEGER,
             PRIMARY KEY(id)
           )"""
    ),
    40000,
    1000,
)

print("2. load 1000 movies")
loaded = 0
with open("/work/data/tmdb-movies-2008.json") as fh:
    for line in fh:
        if loaded >= 1000:
            break
        m = json.loads(line)
        date = m.get("release_date") or ""
        handle.put(
            PutRequest()
            .set_table_name("movies")
            .set_value(
                {
                    "id": m["id"],
                    "title": m.get("title") or "",
                    "year": int(date[:4]) if date[:4].isdigit() else 0,
                    "language": m.get("original_language") or "",
                    "rating": float(m.get("vote_average") or 0),
                    "votes": int(m.get("vote_count") or 0),
                }
            )
        )
        loaded += 1
print(f"   loaded {loaded}")

print("3. query: English movies from 2008 rated 7+ with at least 100 votes")
request = QueryRequest().set_statement(
    "SELECT id, title, rating, votes FROM movies "
    "WHERE year = 2008 AND language = 'en' AND rating >= 7 AND votes >= 100"
)
rows = []
while True:
    result = handle.query(request)
    rows.extend(result.get_results())
    if request.is_done():
        break

for row in sorted(rows, key=lambda r: -r["rating"]):
    print(f"   {row['rating']:>4}  ({row['votes']:>5} votes)  {row['title']}")
print(f"\n   {len(rows)} rows")

handle.close()
