import pandas as pd


GOLD_PATH = "data/processed/gold_annotations.csv"
CANDIDATES_PATH = "data/processed/balanced_candidates.csv"

VALID_INTENTS = {
    "ACCOUNT_ACCESS",
    "ACCOUNT_SECURITY",
    "BILLING_PAYMENT",
    "SUBSCRIPTION",
    "APP_TECHNICAL",
    "CONTENT",
    "FEATURE",
    "OTHER_SUPPORT",
    "NON_SUPPORT",
    "UNKNOWN",
}


# --------------------------------------------------
# 1. Load files
# --------------------------------------------------

print("Loading files...")

gold = pd.read_csv(GOLD_PATH)
candidates = pd.read_csv(CANDIDATES_PATH)

print(f"Existing gold rows: {len(gold):,}")
print(f"Candidate rows: {len(candidates):,}")


# --------------------------------------------------
# 2. Normalize candidate labels
# --------------------------------------------------

candidates["intent"] = (
    candidates["intent"]
    .fillna("")
    .astype(str)
    .str.strip()
    .str.upper()
)

candidates["candidate_intent"] = (
    candidates["candidate_intent"]
    .fillna("")
    .astype(str)
    .str.strip()
    .str.upper()
)


# --------------------------------------------------
# 3. Keep only manually annotated candidates
# --------------------------------------------------

reviewed = candidates[
    candidates["intent"] != ""
].copy()

print(f"Manually reviewed candidates: {len(reviewed):,}")


# --------------------------------------------------
# 4. Validate intents
# --------------------------------------------------

invalid = reviewed[
    ~reviewed["intent"].isin(VALID_INTENTS)
]

print(f"Invalid labels: {len(invalid):,}")

if len(invalid) > 0:

    print("\n=== INVALID LABELS ===")

    print(
        invalid[
            [
                "thread_id",
                "candidate_intent",
                "intent",
                "initial_customer_message",
            ]
        ].to_string(index=False)
    )

    print(
        "\nFix invalid labels before merging."
    )

    raise SystemExit(1)


# --------------------------------------------------
# 5. Measure heuristic agreement
# --------------------------------------------------

reviewed["heuristic_correct"] = (
    reviewed["candidate_intent"]
    == reviewed["intent"]
)

print("\n=== HEURISTIC AGREEMENT ===")

overall_agreement = (
    reviewed["heuristic_correct"].mean() * 100
)

print(
    f"Overall candidate-label agreement: "
    f"{overall_agreement:.1f}%"
)


print("\n=== AGREEMENT BY CANDIDATE INTENT ===")

agreement_by_intent = (
    reviewed
    .groupby("candidate_intent")
    ["heuristic_correct"]
    .agg(["count", "sum", "mean"])
)

agreement_by_intent["mean"] *= 100

agreement_by_intent = (
    agreement_by_intent.rename(
        columns={
            "count": "reviewed",
            "sum": "correct",
            "mean": "agreement_percentage",
        }
    )
)

print(agreement_by_intent)


# --------------------------------------------------
# 6. Show where heuristic was wrong
# --------------------------------------------------

wrong = reviewed[
    reviewed["heuristic_correct"] == False
]

print("\n=== HEURISTIC MISTAKES ===")

if len(wrong) == 0:
    print("None")

else:
    confusion = pd.crosstab(
        wrong["candidate_intent"],
        wrong["intent"],
    )

    print(confusion)


# --------------------------------------------------
# 7. Prepare rows for gold dataset
# --------------------------------------------------

# The original gold dataset has discovery_label.
# Targeted candidates don't, so create it.

reviewed["discovery_label"] = pd.NA

reviewed["annotation_notes"] = (
    reviewed["annotation_notes"]
    .fillna("")
)


gold_columns = [
    "thread_id",
    "initial_customer_message",
    "customer_context",
    "conversation",
    "discovery_label",
    "intent",
    "needs_context",
    "language_issue",
    "annotation_notes",
]

reviewed_for_gold = reviewed[
    gold_columns
].copy()


# --------------------------------------------------
# 8. Only keep actually annotated rows from old gold
# --------------------------------------------------

gold["intent"] = (
    gold["intent"]
    .fillna("")
    .astype(str)
    .str.strip()
    .str.upper()
)

gold_annotated = gold[
    gold["intent"] != ""
].copy()

print(
    f"\nExisting annotated gold examples: "
    f"{len(gold_annotated):,}"
)


# --------------------------------------------------
# 9. Merge
# --------------------------------------------------

combined = pd.concat(
    [
        gold_annotated,
        reviewed_for_gold,
    ],
    ignore_index=True,
)

before_dedup = len(combined)

combined = combined.drop_duplicates(
    subset=["thread_id"],
    keep="first",
)

duplicates_removed = (
    before_dedup - len(combined)
)

print(
    f"Duplicate thread IDs removed: "
    f"{duplicates_removed:,}"
)


# --------------------------------------------------
# 10. Save
# --------------------------------------------------

combined.to_csv(
    GOLD_PATH,
    index=False,
)

print(
    f"\nUpdated gold dataset: "
    f"{len(combined):,} examples"
)

print(f"Saved to: {GOLD_PATH}")


# --------------------------------------------------
# 11. Final distribution
# --------------------------------------------------

print("\n=== UPDATED GOLD DISTRIBUTION ===")

distribution = (
    combined["intent"]
    .value_counts()
    .sort_index()
)

print(distribution)