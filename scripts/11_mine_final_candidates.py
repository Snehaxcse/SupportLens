import pandas as pd

THREADS_PATH = "data/processed/spotify_threads.csv"
GOLD_PATH = "data/processed/gold_annotations.csv"
OUTPUT_PATH = "data/processed/final_candidates.csv"

RANDOM_STATE = 42

NON_SUPPORT_TARGET = 30
RANDOM_TARGET = 40


print("Loading data...")

threads = pd.read_csv(THREADS_PATH)
gold = pd.read_csv(GOLD_PATH)

print(f"Total threads: {len(threads):,}")
print(f"Existing gold examples: {len(gold):,}")


# --------------------------------------------------
# 1. Exclude anything already in gold
# --------------------------------------------------

gold_ids = set(gold["thread_id"].tolist())

available = threads[
    ~threads["thread_id"].isin(gold_ids)
].copy()

print(f"Available unreviewed threads: {len(available):,}")


# --------------------------------------------------
# 2. Search text
# --------------------------------------------------

available["search_text"] = (
    available["initial_customer_message"]
    .fillna("")
    .astype(str)
    .str.lower()
)


# --------------------------------------------------
# 3. NON_SUPPORT candidates
# --------------------------------------------------

non_support_pattern = (
    r"\bthank(?:s| you)?\b|"
    r"\blove spotify\b|"
    r"\blove you\b|"
    r"\bawesome\b|"
    r"\bamazing\b|"
    r"\bgreat service\b|"
    r"\byou guys rock\b|"
    r"\bbest app\b"
)

non_support_pool = available[
    available["search_text"].str.contains(
        non_support_pattern,
        regex=True,
        na=False,
    )
].copy()

print(
    f"Potential NON_SUPPORT candidates: "
    f"{len(non_support_pool):,}"
)

non_support_sample = non_support_pool.sample(
    n=min(
        NON_SUPPORT_TARGET,
        len(non_support_pool),
    ),
    random_state=RANDOM_STATE,
).copy()

non_support_sample["candidate_source"] = (
    "NON_SUPPORT_HEURISTIC"
)


# --------------------------------------------------
# 4. Fresh random taxonomy stress test
# --------------------------------------------------

used_ids = set(
    non_support_sample["thread_id"].tolist()
)

random_pool = available[
    ~available["thread_id"].isin(used_ids)
].copy()

random_sample = random_pool.sample(
    n=min(
        RANDOM_TARGET,
        len(random_pool),
    ),
    random_state=RANDOM_STATE + 1,
).copy()

random_sample["candidate_source"] = (
    "RANDOM_TAXONOMY_CHECK"
)


# --------------------------------------------------
# 5. Combine
# --------------------------------------------------

candidates = pd.concat(
    [
        non_support_sample,
        random_sample,
    ],
    ignore_index=True,
)

candidates = candidates[
    [
        "thread_id",
        "initial_customer_message",
        "customer_context",
        "conversation",
        "candidate_source",
    ]
].copy()


# --------------------------------------------------
# 6. Annotation fields
# --------------------------------------------------

candidates["intent"] = ""
candidates["needs_context"] = False
candidates["language_issue"] = False
candidates["annotation_notes"] = ""


# --------------------------------------------------
# 7. Save
# --------------------------------------------------

candidates.to_csv(
    OUTPUT_PATH,
    index=False,
)

print(
    f"\nCreated {len(candidates):,} "
    f"final annotation candidates."
)

print(
    f"NON_SUPPORT heuristic: "
    f"{len(non_support_sample):,}"
)

print(
    f"Random taxonomy check: "
    f"{len(random_sample):,}"
)

print(f"\nSaved to: {OUTPUT_PATH}")
