"""
applicability.py — Rules for flagging non-applicable product/feature dimensions by industry.
Marks dimensions 'not_applicable' instead of leaving them null/missing.
"""
from typing import Any

# Matrix of non-applicable dimensions by normalized industry category
NON_APPLICABLE_BY_INDUSTRY: dict[str, set[str]] = {
    "software_saas": {
        "physical_locations",
        "in_store_pickup",
        "shipping_methods",
        "inventory_warehouse"
    },
    "retail_e_commerce": {
        "api_rate_limits",
        "sso_saml_integration",
        "multi_tenant_architecture",
        "sdk_availability"
    },
    "fintech_banking": {
        "in_store_pickup",
        "physical_shipping",
        "recipe_customization"
    },
    "healthcare": {
        "e_commerce_cart",
        "table_reservation",
        "guest_checkout"
    },
    "restaurant_hospitality": {
        "sso_saml_integration",
        "api_rate_limits",
        "source_code_export"
    }
}


def get_non_applicable_dimensions(industry: str) -> set[str]:
    """Return set of feature dimension keys that are non-applicable for the industry."""
    ind_norm = (industry or "").lower().strip()
    
    # Direct match or partial match
    for category, dimensions in NON_APPLICABLE_BY_INDUSTRY.items():
        if category in ind_norm or ind_norm in category:
            return dimensions
            
    if "saas" in ind_norm or "software" in ind_norm:
        return NON_APPLICABLE_BY_INDUSTRY["software_saas"]
    if "retail" in ind_norm or "shop" in ind_norm or "commerce" in ind_norm:
        return NON_APPLICABLE_BY_INDUSTRY["retail_e_commerce"]
        
    return set()


def apply_industry_applicability(
    feature_matrix: dict[str, Any],
    industry: str
) -> dict[str, Any]:
    """
    Given a feature matrix dictionary (dimension -> status/detail),
    update dimensions that are non-applicable for the given industry
    to have status 'not_applicable'.
    """
    non_app = get_non_applicable_dimensions(industry)
    updated = dict(feature_matrix)

    for dim in non_app:
        # Mark as not_applicable regardless of whether it was present or missing
        updated[dim] = "not_applicable"

    return updated
