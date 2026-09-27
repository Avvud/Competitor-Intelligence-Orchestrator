"""
industry.py — maps free-text industry labels to normalised categories.

The LLM gives us a label like "Artisan Bakery" or "Project Management SaaS".
We map it to a short snake_case category used for query generation and
dimension applicability logic.

Nothing here is hard-coded to a specific company.
"""

# Map of keywords → normalised category
# Checked in order; first match wins.
_KEYWORD_MAP: list[tuple[list[str], str]] = [
    (["bakery", "bread", "pastry", "patisserie", "cake"],          "food_beverage"),
    (["restaurant", "cafe", "coffee", "food", "dining"],           "food_beverage"),
    (["saas", "software", "platform", "app", "cloud", "tech"],     "saas"),
    (["ecommerce", "e-commerce", "online store", "marketplace"],   "ecommerce"),
    (["retail", "shop", "store"],                                  "retail"),
    (["consulting", "advisory", "services"],                       "professional_services"),
    (["health", "clinic", "medical", "dental", "pharmacy"],        "healthcare"),
    (["education", "school", "tutoring", "training", "edtech"],    "education"),
    (["logistics", "shipping", "freight", "delivery"],             "logistics"),
    (["finance", "fintech", "banking", "insurance", "lending"],    "fintech"),
    (["real estate", "property", "realty"],                        "real_estate"),
    (["media", "publishing", "news", "content"],                   "media"),
    (["manufacturing", "factory", "industrial"],                   "manufacturing"),
    (["travel", "hotel", "hospitality", "tourism"],                "travel"),
    (["agriculture", "farming", "agritech"],                       "agriculture"),
]

_DEFAULT_CATEGORY = "other"


def normalise_industry(label: str) -> str:
    """
    Turn a free-text industry label into a normalised snake_case category.
    Example: "Artisan Bakery" → "food_beverage"
    """
    label_lower = label.lower()
    for keywords, category in _KEYWORD_MAP:
        if any(kw in label_lower for kw in keywords):
            return category
    return _DEFAULT_CATEGORY
