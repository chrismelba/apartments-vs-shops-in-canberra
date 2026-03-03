"""
Configuration for Canberra Shops vs Apartments analysis.
Adjust radii, weights, and scoring here without touching the scripts.
"""

# Canberra bounding box (WGS84)
CANBERRA_CENTER = {"lat": -35.2809, "lng": 149.1300}
CANBERRA_BBOX = {
    "north": -35.05,
    "south": -35.55,
    "east":  149.40,
    "west":  148.99,
}

# Radii
SHOPS_RADIUS_M   = 300   # Nearby search radius around each centre
ZONING_RADIUS_M  = 500   # ACT zoning analysis radius around each centre
DEDUP_RADIUS_M   = 200   # Minimum distance between centres (de-duplicate)

# Google Places API
PLACES_BASE_URL    = "https://maps.googleapis.com/maps/api/place"
PLACES_PAGE_DELAY  = 2   # seconds between paginated requests (API requirement)
PLACE_DETAIL_FIELDS = (
    "place_id,name,types,rating,user_ratings_total,"
    "opening_hours,price_level,geometry"
)

# ACTMAPI ArcGIS REST endpoint for Territory Plan Land Use Zones
ACTMAPI_ZONE_URL = (
    "https://services1.arcgis.com/E5n4f1VY84i0xSjy/arcgis/rest/services/"
    "ACTGOV_TP_LAND_USE_ZONE/FeatureServer/1/query"
)

# ABS 2021 Census — SA1 boundaries + population
ABS_SA1_URL = (
    "https://geo.abs.gov.au/arcgis/rest/services/ASGS2021/SA1/FeatureServer/0/query"
)
ABS_DATAPACK_URL = (
    "https://www.abs.gov.au/census/find-census-data/datapacks/download/"
    "2021_GCP_SA1_for_ACT_short-header.zip"
)
POPULATION_RADIUS_M = 500  # same buffer as zoning

# Zone score mapping (RZ = residential zone; higher = denser)
ZONE_SCORES = {
    "RZ1": 1,   # Suburban (detached houses)
    "RZ2": 2,   # Suburban Core
    "RZ3": 3,   # Urban Residential
    "RZ4": 4,   # Urban Core
    "RZ5": 5,   # High Density Residential
    "CZ1": 0,   # Commercial Core (not residential)
    "CZ2": 0,
    "CZ3": 0,
    "CZ4": 0,
    "CZ5": 0,
    "NUZ": 0,   # Non-Urban Zone
    "PRZ": 0,   # Parks & Recreation
    "CFZ": 0,   # Community Facility
    "TSZ": 0,   # Transport / Services
    "IZ1": 0,   # Industrial
    "IZ2": 0,
}

# Shop quality scoring weights (must sum to 1.0)
SCORE_WEIGHTS = {
    "avg_rating":      0.25,
    "review_density":  0.20,
    "variety":         0.35,
    "hours":           0.20,
}

# Variety scoring: Google Places types → points
# Max possible = 12 pts (normalised to 0–10 in analyse.py)
VARIETY_MAX_POINTS = 12
VARIETY_TYPES = {
    "supermarket":             3,
    "grocery_or_supermarket":  3,
    "convenience_store":       1,
    "cafe":                    1,
    "coffee_shop":             1,
    "restaurant":              1,
    "food":                    1,
    "meal_takeaway":           1,
    "pharmacy":                2,
    "drugstore":               2,
    "doctor":                  2,
    "hospital":                2,
    "medical":                 2,
    "bakery":                  1,
    "gym":                     1,
    "fitness_center":          1,
    "bank":                    1,
    "post_office":             1,
}
