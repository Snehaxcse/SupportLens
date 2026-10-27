import pandas as pd
from pathlib import Path

INPUT_PATH = "data/processed/taxonomy_sample.csv"
OUTPUT_PATH = "data/processed/gold_annotations.csv"


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


print("Loading taxonomy sample...")

df = pd.read_csv(INPUT_PATH)

print(f"Examples: {len(df):,}")


# Preserve your discovery labels for reference
if "possible_intent" in df.columns:
    df = df.rename(
        columns={
            "possible_intent": "discovery_label"
        }
    )


# Create final annotation columns
df["intent"] = ""
df["needs_context"] = False
df["language_issue"] = False
df["annotation_notes"] = ""


columns = [
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

# Only select columns that actually exist
columns = [
    column
    for column in columns
    if column in df.columns
]

df = df[columns]


Path("data/processed").mkdir(
    parents=True,
    exist_ok=True
)

df.to_csv(
    OUTPUT_PATH,
    index=False
)

print(f"Saved annotation file to: {OUTPUT_PATH}")

print("\nAllowed intents:")

for intent in sorted(VALID_INTENTS):
    print(f" - {intent}")