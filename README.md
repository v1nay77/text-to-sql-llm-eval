# Text-to-SQL LLM Evaluation

Benchmarks how well large language models turn natural-language questions into correct SQL, using a real cricket database: every IPL match and ball-by-ball delivery from **2021–2024**.

Each model gets the database schema and a question and must return one SQLite query. That query is run against the database, and its **result** is compared to the result of a hand-written gold query. This metric is called *execution accuracy*: a model is scored on whether it got the right answer, not on whether its SQL matches the gold query's wording.

## Results

20 questions (8 *hard*, 12 *brutal*), temperature 0, all models called through [OpenRouter](https://openrouter.ai).

| Model            | Score        | Hard (8) | Brutal (12) | Avg latency / question |
|------------------|--------------|----------|-------------|------------------------|
| Claude Sonnet 5  | **19 / 20 (95%)** | 8 | 11 | 2.83 s |
| Grok 4.5         | 17 / 20 (85%)     | 8 | 9  | 12.71 s |
| GPT-5.6 Terra    | 16 / 20 (80%)     | 7 | 9  | 5.24 s |
| Kimi K3          | 12 / 20 (60%)     | 7 | 5  | 3.69 s |
| MiniMax M3       | 10 / 20 (50%)     | 6 | 4  | 5.59 s |

The per-question results, including every generated query, are in [`eval_results.csv`](eval_results.csv).

## The dataset

`ipl_2021_2024.db` is a SQLite database with two linked tables, built from the Kaggle *IPL Complete Dataset*:

- **`matches`**: one row per match (season, venue, teams, toss, winner, result margin, …)
- **`deliveries`**: one row per ball (`match_id` → `matches.id`, innings, over, batter, bowler, runs, extras, wickets, …)

The full schema is in [`schema.sql`](schema.sql). This is the exact text the models see in their prompt.

## The golden questions

The questions are written to catch the mistakes models typically make with real-world SQL. Each one contains one or more **traps**:

- **NULL-sensitive filters**: `extras_type IS NULL OR extras_type NOT IN (...)`. A plain `NOT IN` silently drops legal balls.
- **Ratios and rates**: strike rate, economy, bowling average, percentages
- **`HAVING` thresholds**: minimum runs, balls, wickets or matches
- **Legal-ball counting**: excluding wides and no-balls correctly
- **Wicket attribution**: run outs, retired hurt and similar don't count for the bowler
- **Innings and phase logic**: powerplay (overs 0–5), death overs (15–19), batting first vs. chasing
- **Self-referential team logic**: `team1` / `team2` / `winner`, window functions, nested subqueries

`hard` questions have 1–2 traps, and `brutal` questions have 3 or more.

## How scoring works

[`evaluator.py`](evaluator.py) compares the gold result and the generated result **by value**:

- Column names are ignored.
- Numbers are compared numerically (`24395 == 24395.0`), with small floating-point differences tolerated.
- Row order is ignored unless the question is marked `order_sensitive`.
- **Flexible column count**: a "who scored the most?" answer counts as correct whether it returns just the name or the name plus the count. One extra or one missing column is allowed, as long as the smaller result's values appear in the larger one.

Each question gets one outcome: `match`, `mismatch`, `sql_error` (the query failed to run) or `gen_error` (the API call failed).

## Project structure

| File | Purpose |
|------|---------|
| `db.py` | Builds `ipl_2021_2024.db` from `data/matches.csv` and `data/deliveries.csv` |
| `schema_extractor.py` | Prints the schema and sample rows, and writes `schema.sql`. Can also make a renamed copy of the schema, so models can't lean on memorized Kaggle column names |
| `golden_dataset_generator.py` | The 20 golden questions and gold SQL. Run it on its own to check that every gold query executes |
| `make_golden_dataset.py` | Runs the gold SQL and writes the questions with their expected results to `golden_dataset.csv` |
| `model_openrouter_slug.py` | The models under test, as `(display name, OpenRouter slug)` |
| `evaluator.py` | Logic for comparing results |
| `main.py` | Runs the evaluation: generate SQL → execute → score → log latency → write `eval_results.csv` |
| `first_test.py` | Smoke test: sends one question to one model and prints the raw output |

## Getting started

**1. Install the dependencies** (Python 3.10+):

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (macOS/Linux: source .venv/bin/activate)
pip install langchain-openrouter python-dotenv pandas
```

**2. Add your OpenRouter API key** in a `.env` file in the project root:

```
OPENROUTER_API_KEY=sk-or-...
```

**3. Run the evaluation:**

```bash
python main.py
```

This prints a live verdict for each question and a final scoreboard, and writes the details to `eval_results.csv`.

### Rebuilding from scratch (optional)

The database, schema and golden dataset are already in the repo. To rebuild them, put the Kaggle `matches.csv` and `deliveries.csv` in `data/` and run:

```bash
python db.py                      # build ipl_2021_2024.db
python schema_extractor.py        # write schema.sql
python make_golden_dataset.py     # write golden_dataset.csv
```

### Adding models or questions

- **Models**: add a `(name, slug)` pair to `MODELS` in `model_openrouter_slug.py`. Any model on OpenRouter works.
- **Questions**: add an entry to `GOLDEN` in `golden_dataset_generator.py`, then run `make_golden_dataset.py` again. If row order matters for the new question, add its id to `ORDER_SENSITIVE_IDS`.
