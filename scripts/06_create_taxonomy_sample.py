import pandas as pd
from pathlib import Path

INPUT_PATH = "data/processed/spotify_threads.csv"
OUTPUT_PATH = "data/processed/taxonomy_sample.csv"

SAMPLE_SIZE = 250
RANDOM_STATE = 42


print("Loading Spotify threads...")

df = pd.read_csv(INPUT_PATH)

print(f"Total threads: {len(df):,}")


# --------------------------------------------------
# 1. Basic usability filtering
# --------------------------------------------------

df["initial_customer_message"] = (
    df["initial_customer_message"]
    .fillna("")
    .str.strip()
)

df["word_count"] = (
    df["initial_customer_message"]
    .str.split()
    .str.len()
)

# Keep messages with enough content for discovery.
# This is only a discovery heuristic.
usable = df[
    df["word_count"] >= 4
].copy()

print(
    f"Threads after basic filtering: "
    f"{len(usable):,}"
)


# --------------------------------------------------
# 2. Remove duplicate initial problems
# --------------------------------------------------

usable = usable.drop_duplicates(
    subset=["initial_customer_message"]
)

print(
    f"After duplicate removal: "
    f"{len(usable):,}"
)


# --------------------------------------------------
# 3. Sample threads
# --------------------------------------------------

sample_size = min(
    SAMPLE_SIZE,
    len(usable)
)

sample = usable.sample(
    n=sample_size,
    random_state=RANDOM_STATE
).copy()


# --------------------------------------------------
# 4. Create discovery worksheet
# --------------------------------------------------

sample = sample[
    [
        "thread_id",
        "initial_customer_message",
        "customer_context",
        "conversation",
        "message_count",
    ]
].copy()

sample["possible_intent"] = ""
sample["notes"] = ""


# --------------------------------------------------
# 5. Save
# --------------------------------------------------

Path("data/processed").mkdir(
    parents=True,
    exist_ok=True
)

sample.to_csv(
    OUTPUT_PATH,
    index=False
)

print(
    f"\nCreated taxonomy sample: "
    f"{len(sample):,} threads"
)

print(f"Saved to: {OUTPUT_PATH}")


# --------------------------------------------------
# 6. Preview
# --------------------------------------------------

print("\n=== FIRST 5 ===")

for _, row in sample.head(5).iterrows():

    print("\n" + "=" * 80)

    print("\nINITIAL PROBLEM:")
    print(row["initial_customer_message"])

    print("\nFULL THREAD:")
    print(row["conversation"])

print("\n" + "=" * 80)
