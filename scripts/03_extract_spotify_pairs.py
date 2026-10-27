import pandas as pd
from pathlib import Path

DATA_PATH = "data/raw/twcs.csv"
OUTPUT_PATH = "data/processed/spotify_pairs.csv"
BRAND = "SpotifyCares"

print("Loading dataset...")
df = pd.read_csv(DATA_PATH)

# --------------------------------------------------
# 1. Select Spotify's outbound support messages
# --------------------------------------------------

spotify_replies = df[
    (df["author_id"] == BRAND) &
    (df["inbound"] == False) &
    (df["in_response_to_tweet_id"].notna())
].copy()

print(f"Spotify replies to another tweet: {len(spotify_replies):,}")


# --------------------------------------------------
# 2. Select inbound customer messages
# --------------------------------------------------

customer_messages = df[
    df["inbound"] == True
][
    ["tweet_id", "author_id", "text", "created_at"]
].copy()

customer_messages = customer_messages.rename(
    columns={
        "tweet_id": "customer_tweet_id",
        "author_id": "customer_id",
        "text": "customer_text",
        "created_at": "customer_created_at",
    }
)


# --------------------------------------------------
# 3. Prepare Spotify replies
# --------------------------------------------------

spotify_replies = spotify_replies[
    [
        "tweet_id",
        "text",
        "created_at",
        "in_response_to_tweet_id",
    ]
].copy()

spotify_replies = spotify_replies.rename(
    columns={
        "tweet_id": "response_tweet_id",
        "text": "response_text",
        "created_at": "response_created_at",
    }
)

# Convert IDs to compatible type
spotify_replies["in_response_to_tweet_id"] = (
    spotify_replies["in_response_to_tweet_id"].astype("Int64")
)


# --------------------------------------------------
# 4. Join customer message with Spotify response
# --------------------------------------------------

pairs = spotify_replies.merge(
    customer_messages,
    left_on="in_response_to_tweet_id",
    right_on="customer_tweet_id",
    how="inner",
)


# --------------------------------------------------
# 5. Keep useful columns
# --------------------------------------------------

pairs = pairs[
    [
        "customer_tweet_id",
        "customer_id",
        "customer_text",
        "customer_created_at",
        "response_tweet_id",
        "response_text",
        "response_created_at",
    ]
]


# --------------------------------------------------
# 6. Save
# --------------------------------------------------

Path("data/processed").mkdir(parents=True, exist_ok=True)

pairs.to_csv(
    OUTPUT_PATH,
    index=False
)

print(f"\nValid customer → Spotify pairs: {len(pairs):,}")
print(f"Saved to: {OUTPUT_PATH}")

print("\n=== SAMPLE PAIRS ===")

sample = pairs.sample(
    min(10, len(pairs)),
    random_state=42
)

for _, row in sample.iterrows():
    print("\nCUSTOMER:")
    print(row["customer_text"])

    print("\nSPOTIFY:")
    print(row["response_text"])

    print("-" * 70)