"""
main.py - controls the entire eval flow.

For each model:
  1. load the model (via OpenRouter, through ChatOpenRouter)
  2. run the golden dataset (generate SQL for each question)
  3. run the generated SQL on the DB (get a result)
  4. evaluate (compare to gold, using evaluator.py)
  5. log the score + latency (total time per model, avg LLM call per question)

Files it depends on:
  - schema.sql             (the schema, for the prompt)
  - golden_dataset.csv     (questions + gold_sql + order_sensitive)
  - model_openrouter_slug.py  (the 5 models under test -> MODELS)
  - evaluator.py           (the comparison logic)

Setup:
  pip install langchain-openrouter python-dotenv pandas
  .env file with:  OPENROUTER_API_KEY=sk-or-...
"""

import os
import re
import json
import time
import sqlite3
import pandas as pd
from dotenv import load_dotenv

from langchain_openrouter import ChatOpenRouter
from langchain_core.messages import SystemMessage, HumanMessage

from model_openrouter_slug import MODELS
from evaluator import evaluate_one

# ---------- CONFIG ----------
load_dotenv()
API_KEY = os.getenv("OPENROUTER_API_KEY")

DB_PATH = "ipl_2021_2024.db"
SCHEMA_PATH = "schema.sql"
GOLDEN_PATH = "golden_dataset.csv"
RESULTS_PATH = "eval_results.csv"
# ----------------------------


# ---------- helpers ----------
def load_schema():
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return f.read().strip()


def load_golden():
    return pd.read_csv(GOLDEN_PATH)


def make_llm(slug):
    """Create a ChatOpenRouter model for one OpenRouter slug."""
    return ChatOpenRouter(
        model=slug,
        openrouter_api_key=API_KEY,
        temperature=0,
        max_tokens=800,          # keep low: SQL is short, avoids credit-reservation error
    )


def clean_sql(raw):
    """Strip markdown fences / prose, return runnable SQL."""
    if not raw:
        return ""
    text = raw.strip()
    fence = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    text = re.sub(r"^\s*sql\s*\n", "", text, flags=re.IGNORECASE)
    m = re.search(r"\b(SELECT|WITH)\b", text, re.IGNORECASE)
    if m:
        text = text[m.start():]
    return text.strip().strip("`").rstrip(";").strip("`").strip()


def generate_sql(question, schema, llm):
    """Ask one model for SQL via ChatOpenRouter. Returns cleaned SQL string."""
    system_msg = (
        "You are a text-to-SQL generator. Given a database schema and a question, "
        "return a single SQL query that answers it. Use SQLite syntax. "
        "Return only the SQL query."
    )
    user_msg = f"Schema:\n{schema}\n\nQuestion: {question}\n\nSQL:"

    response = llm.invoke([
        SystemMessage(content=system_msg),
        HumanMessage(content=user_msg),
    ])

    raw = response.content
    cleaned = clean_sql(raw)
    if not cleaned:
        print(f"  RAW (uncleaned): {repr(raw)[:300]}")

    return cleaned


def run_sql(conn, sql):
    """Run SQL on the DB. Returns a DataFrame, or None if it errored."""
    try:
        return pd.read_sql_query(sql, conn)
    except Exception:
        return None


def gold_result_to_df(gold_result_json):
    """Rebuild the gold result DataFrame from the stored JSON."""
    obj = json.loads(gold_result_json)
    return pd.DataFrame(obj["rows"], columns=obj["columns"])


# ---------- the main flow ----------
def run_eval():
    schema = load_schema()
    golden = load_golden()
    conn = sqlite3.connect(DB_PATH)

    all_rows = []
    scoreboard = {}
    latency = {}                      # name -> (total_sec, avg_gen_sec)

    for name, slug in MODELS:
        print(f"\n{'='*60}\nMODEL: {name}  ({slug})\n{'='*60}")
        llm = make_llm(slug)          # 1. load the model
        correct = 0
        gen_times = []
        model_start = time.perf_counter()

        for _, g in golden.iterrows():
            qid = g["id"]
            question = g["question"]
            order_sensitive = str(g["order_sensitive"]).upper() == "TRUE"
            gold_df = gold_result_to_df(g["gold_result_json"])

            # 2. generate SQL
            t0 = time.perf_counter()
            try:
                sql = generate_sql(question, schema, llm)
            except Exception as e:
                gen_sec = time.perf_counter() - t0
                gen_times.append(gen_sec)
                print(f"  #{qid:2} [{g['difficulty']:6}] GEN-ERROR ({gen_sec:.2f}s): {e}")
                all_rows.append(dict(model=name, id=qid, difficulty=g["difficulty"],
                                     correct=False, reason="gen_error", sql="",
                                     latency_sec=round(gen_sec, 3)))
                continue
            gen_sec = time.perf_counter() - t0
            gen_times.append(gen_sec)

            # 3. run on DB
            gen_df = run_sql(conn, sql)

            # 4. evaluate
            verdict = evaluate_one(gold_df, gen_df, order_sensitive)
            if verdict["correct"]:
                correct += 1

            mark = "OK " if verdict["correct"] else "XX "
            print(f"  #{qid:2} [{g['difficulty']:6}] {mark} ({gen_sec:5.2f}s) {verdict['reason']}")
            all_rows.append(dict(model=name, id=qid, difficulty=g["difficulty"],
                                 correct=verdict["correct"], reason=verdict["reason"],
                                 sql=sql, latency_sec=round(gen_sec, 3)))

        total = len(golden)
        scoreboard[name] = correct
        model_sec = time.perf_counter() - model_start
        avg_gen = sum(gen_times) / len(gen_times) if gen_times else 0.0
        latency[name] = (model_sec, avg_gen)
        print(f"\n  SCORE: {correct}/{total} = {100*correct/total:.1f}%")
        print(f"  TIME : {model_sec:.2f}s total, {avg_gen:.2f}s avg per question (LLM call)")

    conn.close()

    # 5. log final scores
    print(f"\n{'='*60}\nFINAL SCOREBOARD (execution accuracy + latency)\n{'='*60}")
    total = len(golden)
    for name, _ in MODELS:
        c = scoreboard[name]
        model_sec, avg_gen = latency[name]
        print(f"  {name:18s} {c:2}/{total}  = {100*c/total:5.1f}%   "
              f"total {model_sec:7.2f}s   avg {avg_gen:5.2f}s/q")

    pd.DataFrame(all_rows).to_csv(RESULTS_PATH, index=False)
    print(f"\nDetailed results saved -> {RESULTS_PATH}")


if __name__ == "__main__":
    if not API_KEY:
        print("Missing OPENROUTER_API_KEY - add it to your .env file")
    else:
        run_eval()