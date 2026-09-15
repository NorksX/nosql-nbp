### Latency by query, model and database (p50, ms)

| # | Query | Oracle L1 | Oracle L2 | FoundationDB L1 | FoundationDB L2 | PostgreSQL L1 | PostgreSQL L2 | Faster model |
|---|---|--:|--:|--:|--:|--:|--:|---|
| 1 | Point lookup by TMDB id | **0.59** | **6,610** ᶠ | **2.02** | **715.38** ᶠ | **0.06** | **72.63** ᶠ | Oracle L1 11,165× · FoundationDB L1 354× · PostgreSQL L1 1,231× |
| 2 | Lookup by IMDb id (alternate key) | **1.18** | **6,560** ᶠ | **1.97** | **707.59** ᶠ | **0.06** | **70.39** ᶠ | Oracle L1 5,573× · FoundationDB L1 359× · PostgreSQL L1 1,154× |
| 3 | All movies of year 2017 | **9.17** | **20.61** | **24.85** | **69.49** | **0.51** | **0.07** | Oracle L1 2.2× · FoundationDB L1 2.8× · PostgreSQL L2 7.8× |
| 4 | Language 'en' with vote_count > 500 | **4.62** | **6,698** ᶠ | **8.04** | **769.46** ᶠ | **0.66** | **81.21** ᶠ | Oracle L1 1,449× · FoundationDB L1 96× · PostgreSQL L1 123× |
| 5 | Movies in both Drama and Horror | **161.85** | **6,584** ᶠ | **131.62** | **756.00** ᶠ | **1.02** | **83.14** ᶠ | Oracle L1 41× · FoundationDB L1 5.7× · PostgreSQL L1 82× |
| 6 | Top 20 of Drama by popularity | **627.06** ᶠ | **1.97** | **4.65** | **2.36** | **0.10** | **0.07** | Oracle L2 319× · FoundationDB L2 2.0× · PostgreSQL L2 1.5× |
| 7 | Same language, +/-1 year of Ad Astra | **289.59** ᶠ | **1,054** ᶠ | **310.58** ᶠ | **157.43** | **2.78** | **13.01** | Oracle L1 3.6× · FoundationDB L2 2.0× · PostgreSQL L1 4.7× |
| 8 | Avg rating & count per genre per year, 2000-2020 | **881.28** ᶠ | **9.93** | **1,263** ᶠ | **9.03** | **51.16** ᶠ | **0.29** | Oracle L2 89× · FoundationDB L2 140× · PostgreSQL L2 174× |
| 9 | Top 10 languages by count, mean popularity | **517.82** ᶠ | **3.01** | **1,278** ᶠ | **4.03** | **22.74** ᶠ | **0.13** | Oracle L2 172× · FoundationDB L2 317× · PostgreSQL L2 176× |
| 10 | Yearly trend, vote_count >= 50 | **934.15** ᶠ | **1.24** | **1,259** ᶠ | **2.00** | **26.69** ᶠ | **0.11** | Oracle L2 755× · FoundationDB L2 629× · PostgreSQL L2 245× |

ᶠ = no index path for this model; the query degrades to a full scan.

### Same query, same model, different database

| # | Model | Oracle NoSQL | FoundationDB | PostgreSQL | Fastest | Spread |
|---|---|--:|--:|--:|---|---|
| 1 | L1 | **0.59** | **2.02** | **0.06** | PostgreSQL | 34× |
| 1 | L2 | **6,610** ᶠ | **715.38** ᶠ | **72.63** ᶠ | PostgreSQL | 91× |
| 2 | L1 | **1.18** | **1.97** | **0.06** | PostgreSQL | 32× |
| 2 | L2 | **6,560** ᶠ | **707.59** ᶠ | **70.39** ᶠ | PostgreSQL | 93× |
| 3 | L1 | **9.17** | **24.85** | **0.51** | PostgreSQL | 49× |
| 3 | L2 | **20.61** | **69.49** | **0.07** | PostgreSQL | 1,069× |
| 4 | L1 | **4.62** | **8.04** | **0.66** | PostgreSQL | 12× |
| 4 | L2 | **6,698** ᶠ | **769.46** ᶠ | **81.21** ᶠ | PostgreSQL | 82× |
| 5 | L1 | **161.85** | **131.62** | **1.02** | PostgreSQL | 159× |
| 5 | L2 | **6,584** ᶠ | **756.00** ᶠ | **83.14** ᶠ | PostgreSQL | 79× |
| 6 | L1 | **627.06** ᶠ | **4.65** | **0.10** | PostgreSQL | 5,972× |
| 6 | L2 | **1.97** | **2.36** | **0.07** | PostgreSQL | 35× |
| 7 | L1 | **289.59** ᶠ | **310.58** ᶠ | **2.78** | PostgreSQL | 112× |
| 7 | L2 | **1,054** ᶠ | **157.43** | **13.01** | PostgreSQL | 81× |
| 8 | L1 | **881.28** ᶠ | **1,263** ᶠ | **51.16** ᶠ | PostgreSQL | 25× |
| 8 | L2 | **9.93** | **9.03** | **0.29** | PostgreSQL | 34× |
| 9 | L1 | **517.82** ᶠ | **1,278** ᶠ | **22.74** ᶠ | PostgreSQL | 56× |
| 9 | L2 | **3.01** | **4.03** | **0.13** | PostgreSQL | 31× |
| 10 | L1 | **934.15** ᶠ | **1,259** ᶠ | **26.69** ᶠ | PostgreSQL | 47× |
| 10 | L2 | **1.24** | **2.00** | **0.11** | PostgreSQL | 18× |

### Where each database wins

| Database | Model | Fastest on |
|---|---|---|
| Oracle NoSQL | L1 | — |
| Oracle NoSQL | L2 | — |
| FoundationDB | L1 | — |
| FoundationDB | L2 | — |
| PostgreSQL | L1 | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10 |
| PostgreSQL | L2 | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10 |

### Model R — the normalized relational schema

Each key-value store at its best (the faster of L1 and L2) against PostgreSQL used relationally.

| # | Query | Oracle NoSQL | FoundationDB | PostgreSQL R | Fastest |
|---|---|--:|--:|--:|---|
| 1 | Point lookup by TMDB id | **0.59** | **2.02** | **0.05** | PostgreSQL |
| 2 | Lookup by IMDb id (alternate key) | **1.18** | **1.97** | **0.05** | PostgreSQL |
| 3 | All movies of year 2017 | **9.17** | **24.85** | **0.22** | PostgreSQL |
| 4 | Language 'en' with vote_count > 500 | **4.62** | **8.04** | **0.32** | PostgreSQL |
| 5 | Movies in both Drama and Horror | **161.85** | **131.62** | **1.59** | PostgreSQL |
| 6 | Top 20 of Drama by popularity | **1.97** | **2.36** | **0.12** | PostgreSQL |
| 7 | Same language, +/-1 year of Ad Astra | **289.59** ᶠ | **157.43** | **2.10** | PostgreSQL |
| 8 | Avg rating & count per genre per year, 2000-2020 | **9.93** | **9.03** | **17.90** ᶠ | FoundationDB |
| 9 | Top 10 languages by count, mean popularity | **3.01** | **4.03** | **8.43** ᶠ | Oracle |
| 10 | Yearly trend, vote_count >= 50 | **1.24** | **2.00** | **8.14** ᶠ | Oracle |

ᶠ = no index path; the query reads the whole corpus.

### Tail behaviour (p95 / p50)

| Database | # | Model | p50 ms | p95 ms | p95/p50 |
|---|---|---|--:|--:|--:|
| postgresql | 6 | L2 | 0.07 | 0.20 | 2.96 |
| postgresql | 7 | L1 | 2.78 | 5.68 | 2.04 |
| oracle-nosql | 4 | L1 | 4.62 | 8.48 | 1.83 |
| foundationdb | 6 | L2 | 2.36 | 4.19 | 1.77 |
| oracle-nosql | 2 | L1 | 1.18 | 1.79 | 1.52 |
| foundationdb | 10 | L2 | 2.00 | 2.98 | 1.49 |
