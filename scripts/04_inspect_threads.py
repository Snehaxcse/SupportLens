import pandas as pd

DATA_PATH = "data/raw/twcs.csv"
BRAND = "SpotifyCares"

print("Loading dataset...")
df = pd.read_csv(DATA_PATH)

# Fast tweet lookup
tweet_lookup = df.set_index("tweet_id")


def walk_back(tweet_id, max_depth=10):
    """
    Walk backwards through a conversation using
    in_response_to_tweet_id.
    """

    thread = []
    current_id = tweet_id

    for _ in range(max_depth):

        if current_id not in tweet_lookup.index:
            break

        row = tweet_lookup.loc[current_id]

        thread.append({
            "tweet_id": current_id,
            "author": row["author_id"],
            "inbound": row["inbound"],
            "text": row["text"],
        })

        parent_id = row["in_response_to_tweet_id"]

        if pd.isna(parent_id):
            break

        current_id = int(parent_id)

    # We walked backwards, so reverse into chronological order
    return list(reversed(thread))


# Get Spotify replies that are part of conversations
spotify_tweets = df[
    (df["author_id"] == BRAND) &
    (df["inbound"] == False) &
    (df["in_response_to_tweet_id"].notna())
]

sample = spotify_tweets.sample(
    min(10, len(spotify_tweets)),
    random_state=7
)

for _, spotify_row in sample.iterrows():

    thread = walk_back(
        spotify_row["tweet_id"]
    )

    print("\n" + "=" * 80)

    for message in thread:

        speaker = (
            "CUSTOMER"
            if message["inbound"]
            else message["author"]
        )

        print(f"\n[{speaker}]")
        print(message["text"])

print("\n" + "=" * 80)