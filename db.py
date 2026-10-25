"""
Build a SQLite DB of IPL 2021-2024 from the Kaggle IPL complete dataset.
Expects matches.csv and deliveries.csv in the same folder.
Produces: ipl_2021_2024.db  with two linked tables: matches, deliveries
"""

import sqlite3
import pandas as pd

MATCHES_CSV = "data/matches.csv"
DELIVERIES_CSV = "data/deliveries.csv"
DB_PATH = "ipl_2021_2024.db"
YEARS = {2021, 2022, 2023, 2024}

#Load matches
matches = pd.read_csv(MATCHES_CSV)

#Breakingdown date and extract the years (2021 to 2024).
matches["date"] = pd.to_datetime(matches["date"], errors="coerce", dayfirst=False)

#trying dayfirst as fallback.
if matches["date"].isna().any():
    fallback = pd.to_datetime(
        matches.loc[matches["date"].isna(), "date"] if False else
        pd.read_csv(MATCHES_CSV)["date"],
        errors="coerce", dayfirst=True
    )
    matches["date"] = matches["date"].fillna(fallback)

matches["year"] = matches["date"].dt.year

matches_filtered = matches[matches["year"].isin(YEARS)].copy()

#Match identifier column is named 'id' in this dataset.
match_ids = set(matches_filtered["id"].unique())
print(f"Matches in 2021-2024: {len(matches_filtered)}")

#Loading deliveries, filter to those matches
# deliveries.csv is large file, read then filter on match_id.
deliveries = pd.read_csv(DELIVERIES_CSV)

# The FK column linking deliveries -> matches is 'match_id'.
deliveries_filtered = deliveries[deliveries["match_id"].isin(match_ids)].copy()
print(f"Deliveries for those matches: {len(deliveries_filtered)}")

# ---------- 3. Write to SQLite ----------
conn = sqlite3.connect(DB_PATH)

# drop the helper 'year'
matches_to_save = matches_filtered.drop(columns=["year"])

matches_to_save.to_sql("matches", conn, if_exists="replace", index=False)
deliveries_filtered.to_sql("deliveries", conn, if_exists="replace", index=False)

#Add indexes so JOINs/queries are fast
cur = conn.cursor()
cur.execute("CREATE INDEX IF NOT EXISTS idx_deliveries_match ON deliveries(match_id)")
cur.execute("CREATE INDEX IF NOT EXISTS idx_matches_id ON matches(id)")
conn.commit()

#A basic review
print("\n--- Verification ---")
print("matches rows:", cur.execute("SELECT COUNT(*) FROM matches").fetchone()[0])
print("deliveries rows:", cur.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0])

# confirm no orphan deliveries (every delivery links to a real match)
orphans = cur.execute("""
    SELECT COUNT(*) FROM deliveries d
    LEFT JOIN matches m ON d.match_id = m.id
    WHERE m.id IS NULL
""").fetchone()[0]
print("orphan deliveries (should be 0):", orphans)

# show the season/year range actually captured
yr = cur.execute("""
    SELECT MIN(date), MAX(date) FROM matches
""").fetchone()
print("date range:", yr)

conn.close()
print(f"\nDone. Wrote {DB_PATH}")