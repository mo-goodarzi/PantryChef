"""Map Food.com tags to a single meal_type and cuisine per recipe.

Recipes often carry several course/cuisine tags, so each mapping is an ordered list:
the first matching tag wins. All tags are also stored in recipe_tags, so nothing is lost.
"""

# (tag, meal_type), most specific course first. "brunch" and "lunch" come last because
# they are often added next to a more specific course tag.
MEAL_TYPE_BY_TAG: list[tuple[str, str]] = [
    ("breakfast", "breakfast"),
    ("main-dish", "main-dish"),
    ("desserts", "dessert"),
    ("appetizers", "appetizer"),
    ("side-dishes", "side-dish"),
    ("salads", "salad"),
    ("soups-stews", "soup"),
    ("beverages", "beverage"),
    ("breads", "bread"),
    ("snacks", "snack"),
    ("condiments-etc", "condiment"),
    ("brunch", "breakfast"),
    ("lunch", "lunch"),
]

# National/regional cuisines, checked before the broad fallbacks below.
SPECIFIC_CUISINES: list[str] = [
    # North America (sub-regions)
    "tex-mex",
    "cajun",
    "creole",
    "soul",
    "amish-mennonite",
    "pennsylvania-dutch",
    "southern-united-states",
    "southwestern-united-states",
    "northeastern-united-states",
    "midwestern",
    "pacific-northwest",
    "californian",
    "hawaiian",
    "native-american",
    "canadian",
    "quebec",
    "ontario",
    "british-columbian",
    "mexican",
    "baja",
    "oaxacan",
    # Central/South America and Caribbean
    "caribbean",
    "cuban",
    "puerto-rican",
    "costa-rican",
    "guatemalan",
    "honduran",
    "brazilian",
    "argentine",
    "chilean",
    "colombian",
    "ecuadorean",
    "peruvian",
    "venezuelan",
    # Europe
    "italian",
    "french",
    "greek",
    "spanish",
    "portuguese",
    "german",
    "austrian",
    "swiss",
    "belgian",
    "dutch",
    "english",
    "irish",
    "scottish",
    "welsh",
    "polish",
    "hungarian",
    "czech",
    "russian",
    "danish",
    "swedish",
    "norwegian",
    "finnish",
    "icelandic",
    "jewish-ashkenazi",
    "jewish-sephardi",
    "georgian",
    # Asia
    "chinese",
    "cantonese",
    "szechuan",
    "hunan",
    "beijing",
    "japanese",
    "korean",
    "thai",
    "vietnamese",
    "cambodian",
    "laotian",
    "malaysian",
    "indonesian",
    "filipino",
    "indian",
    "pakistani",
    "nepalese",
    "mongolian",
    # Middle East and Africa
    "lebanese",
    "turkish",
    "iranian-persian",
    "iraqi",
    "palestinian",
    "saudi-arabian",
    "egyptian",
    "moroccan",
    "libyan",
    "ethiopian",
    "nigerian",
    "south-african",
    "sudanese",
    "somalian",
    "congolese",
    "angolan",
    "namibian",
    # Oceania
    "australian",
    "new-zealand",
    "polynesian",
    "micro-melanesia",
]

# Broad regions, used only when no specific cuisine tag is present.
BROAD_CUISINES: list[str] = [
    "american",
    "north-american",
    "central-american",
    "south-american",
    "european",
    "scandinavian",
    "asian",
    "middle-eastern",
    "african",
    "south-west-pacific",
]


def derive_meal_type(tags: list[str]) -> str | None:
    tag_set = set(tags)
    for tag, meal_type in MEAL_TYPE_BY_TAG:
        if tag in tag_set:
            return meal_type
    return None


def derive_cuisine(tags: list[str]) -> str | None:
    tag_set = set(tags)
    for cuisine in SPECIFIC_CUISINES + BROAD_CUISINES:
        if cuisine in tag_set:
            return cuisine
    return None
