import pandas as pd


INPUT_PATH = "data/processed/gold_annotations.csv"


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


print("Loading annotations...")

df = pd.read_csv(INPUT_PATH)

print(f"Total rows in file: {len(df):,}")


# --------------------------------------------------
# 1. Normalize intent labels
# --------------------------------------------------

df["intent"] = (
    df["intent"]
    .fillna("")
    .astype(str)
    .str.strip()
    .str.upper()
)


# --------------------------------------------------
# 2. Find annotated vs unannotated rows
# --------------------------------------------------

annotated = df[
    df["intent"] != ""
].copy()

unannotated = df[
    df["intent"] == ""
].copy()

print(f"Annotated rows: {len(annotated):,}")
print(f"Unannotated rows: {len(unannotated):,}")


# --------------------------------------------------
# 3. Validate labels
# --------------------------------------------------

invalid = annotated[
    ~annotated["intent"].isin(VALID_INTENTS)
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


# --------------------------------------------------
# 4. Analyze valid annotations
# --------------------------------------------------

valid = annotated[
    annotated["intent"].isin(VALID_INTENTS)
].copy()


print("\n=== CLASS DISTRIBUTION ===")

counts = (
    valid["intent"]
    .value_counts()
)

percentages = (
    valid["intent"]
    .value_counts(normalize=True)
    .mul(100)
    .round(1)
)

distribution = pd.DataFrame({
    "count": counts,
    "percentage": percentages,
})

print(distribution)


# --------------------------------------------------
# 5. Analyze needs_context
# --------------------------------------------------

# Handles TRUE/FALSE whether Excel saved them
# as strings or booleans.

df["needs_context_normalized"] = (
    df["needs_context"]
    .astype(str)
    .str.strip()
    .str.lower()
    .map({
        "true": True,
        "false": False,
    })
)


valid["needs_context_normalized"] = (
    valid["needs_context"]
    .astype(str)
    .str.strip()
    .str.lower()
    .map({
        "true": True,
        "false": False,
    })
)


print("\n=== CONTEXT REQUIREMENT ===")

context_counts = (
    valid["needs_context_normalized"]
    .value_counts(dropna=False)
)

print(context_counts)

context_rate = (
    valid["needs_context_normalized"]
    .eq(True)
    .mean()
    * 100
)

print(
    f"\nNeeds additional context: "
    f"{context_rate:.1f}%"
)


# --------------------------------------------------
# 6. Context requirement by intent
# --------------------------------------------------

print("\n=== CONTEXT RATE BY INTENT ===")

context_by_intent = (
    valid
    .groupby("intent")["needs_context_normalized"]
    .agg(["count", "sum", "mean"])
)

context_by_intent["mean"] = (
    context_by_intent["mean"]
    * 100
)

context_by_intent = (
    context_by_intent
    .rename(
        columns={
            "count": "examples",
            "sum": "needs_context",
            "mean": "context_percentage",
        }
    )
    .sort_values(
        "context_percentage",
        ascending=False,
    )
)

print(context_by_intent)


# --------------------------------------------------
# 7. Save normalized labels
# --------------------------------------------------

df["intent"] = (
    df["intent"]
    .replace("", pd.NA)
)

df.to_csv(
    INPUT_PATH,
    index=False,
)

print(
    "\nNormalized annotations saved "
    "back to gold_annotations.csv"
)