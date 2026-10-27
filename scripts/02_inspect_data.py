import pandas as pd

DATA_PATH = "data/raw/twcs.csv"

BRANDS = [
    "AmazonHelp",
    "AppleSupport",
    "Uber_Support",
    "SpotifyCares",
]

print("Loading dataset...")
df = pd.read_csv(DATA_PATH)

print("\n=== BRAND COMPARISON ===")

for brand in BRANDS:
    brand_tweets = df[
        (df["author_id"] == brand) &
        (df["inbound"] == False)
    ]

    print(f"\n{brand}")
    print("-" * 40)
    print(f"Outbound replies: {len(brand_tweets):,}")

    print("\nExample replies:")

    for text in brand_tweets["text"].sample(
        min(5, len(brand_tweets)),
        random_state=42
    ):
        print(f"  • {text[:250]}")