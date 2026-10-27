import re
from pathlib import Path

import pandas as pd


DATA_PATH = "data/raw/twcs.csv"
OUTPUT_PATH = "data/processed/spotify_threads.csv"

BRAND = "SpotifyCares"
MAX_DEPTH = 30


def clean_text(text):
    """
    Light cleaning for readability.
    Preserve the actual language and meaning.
    """

    if pd.isna(text):
        return ""

    text = str(text)

    # Remove URLs
    text = re.sub(r"https?://\S+", "", text)

    # Remove @mentions
    text = re.sub(r"@\w+", "", text)

    # Normalize whitespace
    text = re.sub(r"\s+", " ", text)

    return text.strip()


print("Loading dataset...")

df = pd.read_csv(DATA_PATH)

print(f"Total tweets: {len(df):,}")


# --------------------------------------------------
# 1. Create fast lookup
# --------------------------------------------------

tweet_lookup = df.set_index("tweet_id")


# --------------------------------------------------
# 2. Find Spotify tweets
# --------------------------------------------------

spotify_tweets = df[
    (df["author_id"] == BRAND) &
    (df["inbound"] == False)
].copy()

spotify_tweet_ids = set(
    spotify_tweets["tweet_id"].tolist()
)

print(f"Spotify tweets: {len(spotify_tweets):,}")


# --------------------------------------------------
# 3. Find terminal Spotify tweets
# --------------------------------------------------
#
# We want the latest Spotify message in a chain,
# rather than reconstructing the same thread from
# every Spotify message.
#
# If another tweet responds to a Spotify tweet,
# that Spotify tweet is not necessarily terminal.
# --------------------------------------------------

tweets_with_responses = set(
    df["in_response_to_tweet_id"]
    .dropna()
    .astype(int)
    .tolist()
)

terminal_spotify = spotify_tweets[
    ~spotify_tweets["tweet_id"].isin(tweets_with_responses)
].copy()

print(
    f"Terminal Spotify tweets: "
    f"{len(terminal_spotify):,}"
)


def walk_back(tweet_id):
    """
    Walk backwards from a tweet through
    in_response_to_tweet_id until the beginning
    of the available conversation.
    """

    messages = []
    current_id = int(tweet_id)
    visited = set()

    for _ in range(MAX_DEPTH):

        # Protect against malformed cycles
        if current_id in visited:
            break

        visited.add(current_id)

        if current_id not in tweet_lookup.index:
            break

        row = tweet_lookup.loc[current_id]

        messages.append({
            "tweet_id": current_id,
            "author_id": row["author_id"],
            "inbound": bool(row["inbound"]),
            "created_at": row["created_at"],
            "text": clean_text(row["text"]),
        })

        parent = row["in_response_to_tweet_id"]

        if pd.isna(parent):
            break

        current_id = int(parent)

    # We traversed newest -> oldest.
    # Reverse to get chronological order.
    messages.reverse()

    return messages


# --------------------------------------------------
# 4. Reconstruct threads
# --------------------------------------------------

records = []

print("\nReconstructing threads...")

for _, row in terminal_spotify.iterrows():

    messages = walk_back(row["tweet_id"])

    if not messages:
        continue

    # Must contain Spotify
    has_spotify = any(
        message["author_id"] == BRAND
        for message in messages
    )

    # Must contain a customer
    has_customer = any(
        message["inbound"]
        for message in messages
    )

    if not has_spotify or not has_customer:
        continue

    customer_messages = [
        message["text"]
        for message in messages
        if message["inbound"] and message["text"]
    ]

    if not customer_messages:
        continue

    # First available customer message in the thread.
    initial_customer_message = customer_messages[0]

    # All customer messages combined.
    customer_context = " ".join(customer_messages)

    # Human-readable conversation.
    conversation_lines = []

    for message in messages:

        if message["inbound"]:
            speaker = "CUSTOMER"
        elif message["author_id"] == BRAND:
            speaker = "SPOTIFY"
        else:
            speaker = message["author_id"]

        conversation_lines.append(
            f"{speaker}: {message['text']}"
        )

    conversation_text = "\n".join(
        conversation_lines
    )

    records.append({
        "thread_id": row["tweet_id"],
        "initial_customer_message":
            initial_customer_message,
        "customer_context":
            customer_context,
        "conversation":
            conversation_text,
        "message_count":
            len(messages),
        "customer_message_count":
            sum(
                message["inbound"]
                for message in messages
            ),
        "spotify_message_count":
            sum(
                message["author_id"] == BRAND
                for message in messages
            ),
    })


# --------------------------------------------------
# 5. Create dataframe
# --------------------------------------------------

threads = pd.DataFrame(records)

print(
    f"Reconstructed threads: "
    f"{len(threads):,}"
)


# --------------------------------------------------
# 6. Remove exact duplicate conversations
# --------------------------------------------------

threads = threads.drop_duplicates(
    subset=["conversation"]
)

print(
    f"After duplicate removal: "
    f"{len(threads):,}"
)


# --------------------------------------------------
# 7. Save
# --------------------------------------------------

Path("data/processed").mkdir(
    parents=True,
    exist_ok=True
)

threads.to_csv(
    OUTPUT_PATH,
    index=False
)

print(f"\nSaved to: {OUTPUT_PATH}")


# --------------------------------------------------
# 8. Show examples
# --------------------------------------------------

print("\n=== SAMPLE THREADS ===")

sample = threads.sample(
    min(10, len(threads)),
    random_state=42
)

for _, row in sample.iterrows():

    print("\n" + "=" * 80)

    print(
        f"MESSAGES: {row['message_count']}"
    )

    print("\nINITIAL CUSTOMER MESSAGE:")
    print(row["initial_customer_message"])

    print("\nFULL CONVERSATION:")
    print(row["conversation"])

print("\n" + "=" * 80)