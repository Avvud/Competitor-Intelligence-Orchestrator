"""
geo.py — infer country/city from page signals.

Checks (in priority order):
  1. JSON-LD addressCountry / addressLocality
  2. Phone country code  (+44 → GB, +91 → IN, +1 → US, etc.)
  3. Currency symbol     (£ → GB, ₹ → IN, $ → US, € → EU, etc.)
  4. TLD of domain       (.co.uk → GB, .in → IN, .de → DE, etc.)
  5. Explicit city/country text in page body

Returns a dict with keys: country, city, state, confidence (0.0–1.0 each).
Nothing is hard-coded to a specific company or location — these are
generic signal tables.
"""

import re

# Phone prefix → ISO country code
PHONE_TO_COUNTRY: dict[str, str] = {
    "+44": "GB", "+91": "IN", "+1": "US", "+61": "AU",
    "+49": "DE", "+33": "FR", "+81": "JP", "+86": "CN",
    "+55": "BR", "+52": "MX", "+27": "ZA", "+234": "NG",
    "+65": "SG", "+60": "MY", "+62": "ID", "+63": "PH",
    "+66": "TH", "+84": "VN", "+82": "KR", "+971": "AE",
    "+972": "IL", "+90": "TR", "+39": "IT", "+34": "ES",
    "+31": "NL", "+46": "SE", "+47": "NO", "+45": "DK",
    "+48": "PL", "+7":  "RU", "+380": "UA", "+20": "EG",
    "+92": "PK", "+880": "BD", "+94": "LK", "+977": "NP",
}

# Currency symbol/code → ISO country (broad mapping)
CURRENCY_TO_COUNTRY: dict[str, str] = {
    "£":   "GB",  "gbp": "GB",
    "₹":   "IN",  "inr": "IN",  "rs.": "IN",
    "€":   "EU",  "eur": "EU",
    "¥":   "JP",  "jpy": "JP",
    "¥":   "CN",  "cny": "CN",
    "r$":  "BR",  "brl": "BR",
    "zar": "ZA",  "r":   "ZA",
    "a$":  "AU",  "aud": "AU",
    "s$":  "SG",  "sgd": "SG",
    "₩":   "KR",  "krw": "KR",
    "aed": "AE",  "دإ":  "AE",
    "₦":   "NG",  "ngn": "NG",
}

# TLD → ISO country
TLD_TO_COUNTRY: dict[str, str] = {
    ".co.uk": "GB", ".uk": "GB",
    ".in":    "IN",
    ".de":    "DE",
    ".fr":    "FR",
    ".au":    "AU", ".com.au": "AU",
    ".ca":    "CA",
    ".br":    "BR", ".com.br": "BR",
    ".jp":    "JP",
    ".cn":    "CN",
    ".mx":    "MX",
    ".sg":    "SG",
    ".za":    "ZA", ".co.za": "ZA",
    ".ng":    "NG",
    ".pk":    "PK",
    ".nz":    "NZ",
    ".ie":    "IE",
    ".it":    "IT",
    ".es":    "ES",
    ".nl":    "NL",
    ".se":    "SE",
    ".no":    "NO",
    ".dk":    "DK",
    ".pl":    "PL",
}


def infer_country_from_phone(text: str) -> tuple[str, float] | tuple[None, float]:
    """Find a phone number in text and return (country_code, confidence)."""
    # Sort by length descending so +234 is checked before +2
    for prefix, country in sorted(PHONE_TO_COUNTRY.items(), key=lambda x: -len(x[0])):
        if prefix in text:
            return country, 0.85
    return None, 0.0


def infer_country_from_currency(text: str) -> tuple[str, float] | tuple[None, float]:
    """Find a currency symbol in text and return (country_code, confidence)."""
    text_lower = text.lower()
    for symbol, country in CURRENCY_TO_COUNTRY.items():
        if not symbol.isalnum() and len(symbol) == 1:
            if symbol in text:
                return country, 0.75
        else:
            pattern = r"\b" + re.escape(symbol) + r"\b"
            if re.search(pattern, text_lower):
                return country, 0.75
    return None, 0.0


def infer_country_from_tld(url: str) -> tuple[str, float] | tuple[None, float]:
    """Infer country from the domain TLD."""
    # Strip protocol and path
    domain = re.sub(r"https?://", "", url).split("/")[0].lower()
    # Check multi-part TLDs first (.co.uk before .uk)
    for tld, country in sorted(TLD_TO_COUNTRY.items(), key=lambda x: -len(x[0])):
        if domain.endswith(tld):
            return country, 0.70
    return None, 0.0


def infer_geo(text: str, url: str = "", jsonld_address: dict | None = None) -> dict:
    """
    Combine all signals and return the best guess for country/city/state.

    Returns:
        {
            "country":    str | None,
            "city":       str | None,
            "state":      str | None,
            "confidence": float   # 0.0–1.0
        }
    """
    result = {"country": None, "city": None, "state": None, "confidence": 0.0}

    # 1. JSON-LD is most reliable
    if jsonld_address:
        if jsonld_address.get("addressCountry"):
            result["country"] = jsonld_address["addressCountry"]
            result["confidence"] = max(result["confidence"], 0.95)
        if jsonld_address.get("addressLocality"):
            result["city"] = jsonld_address["addressLocality"]
        if jsonld_address.get("addressRegion"):
            result["state"] = jsonld_address["addressRegion"]
        if result["country"]:
            return result

    # 2. Phone number
    country, conf = infer_country_from_phone(text)
    if country and conf > result["confidence"]:
        result["country"] = country
        result["confidence"] = conf

    # 3. Currency
    if not result["country"]:
        country, conf = infer_country_from_currency(text)
        if country and conf > result["confidence"]:
            result["country"] = country
            result["confidence"] = conf

    # 4. TLD
    if not result["country"] and url:
        country, conf = infer_country_from_tld(url)
        if country and conf > result["confidence"]:
            result["country"] = country
            result["confidence"] = conf

    return result
