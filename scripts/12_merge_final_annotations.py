import pandas as pd


GOLD_PATH = "data/processed/gold_annotations.csv"
FINAL_PATH = "data/processed/final_candidates.csv"

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


print("Loading files...")

gold = pd.read_csv(GOLD_PATH)
final = pd.read_csv(FINAL_PATH)

print(f"Existing gold examples: {len(gold):,}")
print(f"Final candidates: {len(final):,}")


# --------------------------------------------------
# 1. Normalize labels
# --------------------------------------------------

final["intent"] = (
    final["intent"]
    .fillna("")
    .astype(str)
    .str.strip()
    .str.upper()
)

final["candidate_source"] = (
    final["candidate_source"]
    .fillna("")
    .astype(str)
    .str.strip()
    .str.upper()
)


# --------------------------------------------------
# 2. Check that every row was reviewed
# --------------------------------------------------

unreviewed = final[
    final["intent"] == ""
]

print(f"Unreviewed final candidates: {len(unreviewed):,}")

if len(unreviewed) > 0:

    print("\nSome rows are still missing labels:")

    print(
        unreviewed[
            [
                "thread_id",
                "candidate_source",
                "initial_customer_message",
            ]
        ].to_string(index=False)
    )

    raise SystemExit(
        "\nFinish these annotations before merging."
    )


# --------------------------------------------------
# 3. Validate labels
# --------------------------------------------------

invalid = final[
    ~final["intent"].isin(VALID_INTENTS)
]

print(f"Invalid labels: {len(invalid):,}")

if len(invalid) > 0:

    print("\n=== INVALID LABELS ===")

    print(
        invalid[
            [
                "thread_id",
                "intent",
                "initial_customer_message",
            ]
        ].to_string(index=False)
    )

    raise SystemExit(
        "\nFix invalid labels before merging."
    )


# --------------------------------------------------
# 4. NON_SUPPORT heuristic analysis
# --------------------------------------------------

non_support_candidates = final[
    final["candidate_source"]
    == "NON_SUPPORT_HEURISTIC"
].copy()

non_support_correct = (
    non_support_candidates["intent"]
    == "NON_SUPPORT"
)

non_support_agreement = (
    non_support_correct.mean() * 100
)

print("\n=== NON_SUPPORT HEURISTIC ===")

print(
    f"Reviewed: "
    f"{len(non_support_candidates):,}"
)

print(
    f"Actually NON_SUPPORT: "
    f"{non_support_correct.sum():,}"
)

print(
    f"Agreement: "
    f"{non_support_agreement:.1f}%"
)

print("\nActual labels from NON_SUPPORT mining:")

print(
    non_support_candidates[
        "intent"
    ].value_counts()
)


# --------------------------------------------------
# 5. Random taxonomy stress test
# --------------------------------------------------

random_check = final[
    final["candidate_source"]
    == "RANDOM_TAXONOMY_CHECK"
].copy()

print("\n=== RANDOM TAXONOMY CHECK ===")

print(
    f"Random examples reviewed: "
    f"{len(random_check):,}"
)

print("\nIntent distribution:")

print(
    random_check[
        "intent"
    ].value_counts()
)


other_count = (
    random_check["intent"]
    == "OTHER_SUPPORT"
).sum()

unknown_count = (
    random_check["intent"]
    == "UNKNOWN"
).sum()

covered_count = (
    len(random_check)
    - other_count
    - unknown_count
)

coverage_rate = (
    covered_count
    / len(random_check)
    * 100
)

print(
    f"\nExisting taxonomy covered: "
    f"{covered_count}/{len(random_check)} "
    f"({coverage_rate:.1f}%)"
)

print(
    f"OTHER_SUPPORT: {other_count}"
)

print(
    f"UNKNOWN: {unknown_count}"
)


# --------------------------------------------------
# 6. Context rate for final round
# --------------------------------------------------

final["needs_context_normalized"] = (
    final["needs_context"]
    .astype(str)
    .str.strip()
    .str.lower()
    .map({
        "true": True,
        "false": False,
    })
)

context_rate = (
    final["needs_context_normalized"]
    .eq(True)
    .mean()
    * 100
)

print("\n=== FINAL ROUND CONTEXT ===")

print(
    f"Needs additional context: "
    f"{context_rate:.1f}%"
)


# --------------------------------------------------
# 7. Add provenance
# --------------------------------------------------

# Existing gold examples came from earlier stages.
# Preserve source information for the new examples.

final["annotation_source"] = (
    final["candidate_source"]
)

final["discovery_label"] = pd.NA


# --------------------------------------------------
# 8. Prepare compatible datasets
# --------------------------------------------------

gold["annotation_source"] = (
    gold.get(
        "annotation_source",
        pd.Series(
            ["EARLIER_GOLD"] * len(gold)
        )
    )
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
    "annotation_source",
]

for column in gold_columns:

    if column not in gold.columns:
        gold[column] = pd.NA

    if column not in final.columns:
        final[column] = pd.NA


gold = gold[gold_columns]

final_for_gold = final[
    gold_columns
].copy()


# --------------------------------------------------
# 9. Merge
# --------------------------------------------------

combined = pd.concat(
    [
        gold,
        final_for_gold,
    ],
    ignore_index=True,
)

before = len(combined)

combined = combined.drop_duplicates(
    subset=["thread_id"],
    keep="first",
)

duplicates_removed = (
    before - len(combined)
)

print(
    f"\nDuplicate thread IDs removed: "
    f"{duplicates_removed:,}"
)


# --------------------------------------------------
# 10. Save final gold dataset
# --------------------------------------------------

combined.to_csv(
    GOLD_PATH,
    index=False,
)

print(
    f"\nFinal gold dataset: "
    f"{len(combined):,} examples"
)

print(
    f"Saved to: {GOLD_PATH}"
)


# --------------------------------------------------
# 11. Final distribution
# --------------------------------------------------

print("\n=== FINAL GOLD DISTRIBUTION ===")

distribution = (
    combined["intent"]
    .value_counts()
    .sort_values(
        ascending=False
    )
)

print(distribution)


# --------------------------------------------------
# 12. Overall context dependency
# --------------------------------------------------

combined["needs_context_normalized"] = (
    combined["needs_context"]
    .astype(str)
    .str.strip()
    .str.lower()
    .map({
        "true": True,
        "false": False,
    })
)

overall_context_rate = (
    combined["needs_context_normalized"]
    .eq(True)
    .mean()
    * 100
)

print("\n=== GOLD DATASET CONTEXT ===")

print(
    f"Needs additional context: "
    f"{overall_context_rate:.1f}%"
)


# --------------------------------------------------
# 13. Training-eligible examples
# --------------------------------------------------

training_eligible = combined[
    ~combined["intent"].isin(
        [
            "UNKNOWN",
            "OTHER_SUPPORT",
        ]
    )
]

print(
    f"\nPotential training examples "
    f"(excluding UNKNOWN + OTHER_SUPPORT): "
    f"{len(training_eligible):,}"
)