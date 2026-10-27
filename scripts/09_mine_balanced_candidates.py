import pandas as pd
import re
from pathlib import Path


THREADS_PATH = "data/processed/spotify_threads.csv"
ANNOTATIONS_PATH = "data/processed/gold_annotations.csv"
OUTPUT_PATH = "data/processed/balanced_candidates.csv"

RANDOM_STATE = 42
CANDIDATES_PER_INTENT = 40


PATTERNS = {

    "ACCOUNT_SECURITY": [
        r"\bhack(?:ed|ing)?\b",
        r"\bunauthori[sz]ed\b",
        r"\bsomeone.*account\b",
        r"\baccount.*someone\b",
        r"\bstolen\b",
        r"\bcompromised\b",
        r"\bsuspicious\b",
    ],

    "ACCOUNT_ACCESS": [
        r"\blog ?in\b",
        r"\blogging in\b",
        r"\bcan't log\b",
        r"\bcannot log\b",
        r"\bpassword\b",
        r"\breset.*password\b",
        r"\bemail.*account\b",
        r"\baccess.*account\b",
    ],

    "BILLING_PAYMENT": [
        r"\bcharged?\b",
        r"\bcharging\b",
        r"\bpayment\b",
        r"\bpaid\b",
        r"\brefund\b",
        r"\bcard\b",
        r"\bmoney\b",
        r"\btransaction\b",
        r"\bbill(?:ed|ing)?\b",
    ],

    "SUBSCRIPTION": [
        r"\bpremium\b",
        r"\bsubscription\b",
        r"\bstudent\b",
        r"\bfamily plan\b",
        r"\bfree trial\b",
        r"\btrial\b",
        r"\bcancel.*premium\b",
        r"\bupgrade\b",
        r"\bdowngrade\b",
    ],

    "APP_TECHNICAL": [
        r"\bcrash(?:es|ed|ing)?\b",
        r"\bfreeze(?:s|ing)?\b",
        r"\bbug\b",
        r"\bnot working\b",
        r"\bdoesn't work\b",
        r"\bwon't play\b",
        r"\bkeeps pausing\b",
        r"\boffline\b",
        r"\bconnection\b",
    ],

    "CONTENT": [
        r"\bsong\b",
        r"\btrack\b",
        r"\balbum\b",
        r"\bartist\b",
        r"\bpodcast\b",
        r"\bremoved\b",
        r"\bunavailable\b",
        r"\bmissing\b",
    ],

    "FEATURE": [
        r"\bplaylist\b",
        r"\bshuffle\b",
        r"\bqueue\b",
        r"\blocal files?\b",
        r"\bfeature\b",
        r"\bsort\b",
        r"\bblock\b",
        r"\brepeat\b",
    ],
}


print("Loading threads...")

threads = pd.read_csv(THREADS_PATH)

annotations = pd.read_csv(ANNOTATIONS_PATH)

print(f"Available threads: {len(threads):,}")


# --------------------------------------------------
# 1. Exclude threads already manually inspected
# --------------------------------------------------

annotated_ids = set(
    annotations.loc[
        annotations["intent"].notna(),
        "thread_id"
    ].tolist()
)

threads = threads[
    ~threads["thread_id"].isin(annotated_ids)
].copy()

print(
    f"After excluding annotated threads: "
    f"{len(threads):,}"
)


# --------------------------------------------------
# 2. Normalize searchable text
# --------------------------------------------------

threads["search_text"] = (
    threads["initial_customer_message"]
    .fillna("")
    .astype(str)
    .str.lower()
)


# --------------------------------------------------
# 3. Mine candidates for each intent
# --------------------------------------------------

candidate_frames = []

for intent, patterns in PATTERNS.items():

    combined_pattern = "|".join(
        f"(?:{pattern})"
        for pattern in patterns
    )

    matches = threads[
        threads["search_text"].str.contains(
            combined_pattern,
            regex=True,
            na=False,
        )
    ].copy()

    print(
        f"{intent}: "
        f"{len(matches):,} potential candidates"
    )

    if len(matches) == 0:
        continue

    sample_size = min(
        CANDIDATES_PER_INTENT,
        len(matches),
    )

    sampled = matches.sample(
        n=sample_size,
        random_state=RANDOM_STATE,
    ).copy()

    sampled["candidate_intent"] = intent

    candidate_frames.append(sampled)


# --------------------------------------------------
# 4. Combine
# --------------------------------------------------

candidates = pd.concat(
    candidate_frames,
    ignore_index=True,
)


# A thread can match multiple heuristic categories.
# Keep one copy for the annotation worksheet.

candidates = candidates.drop_duplicates(
    subset=["thread_id"]
).copy()


# --------------------------------------------------
# 5. Annotation columns
# --------------------------------------------------

candidates = candidates[
    [
        "thread_id",
        "initial_customer_message",
        "customer_context",
        "conversation",
        "candidate_intent",
    ]
].copy()

candidates["intent"] = ""
candidates["needs_context"] = False
candidates["language_issue"] = False
candidates["annotation_notes"] = ""


# --------------------------------------------------
# 6. Save
# --------------------------------------------------

Path("data/processed").mkdir(
    parents=True,
    exist_ok=True,
)

candidates.to_csv(
    OUTPUT_PATH,
    index=False,
)

print(
    f"\nCreated {len(candidates):,} "
    f"balanced annotation candidates."
)

print(f"Saved to: {OUTPUT_PATH}")