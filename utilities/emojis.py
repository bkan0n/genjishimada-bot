import math
from typing import Literal

CONFIRM = "<:_:1052666519487795261>"
UNVERIFIED = "<:_:1042541865821556746>"

TIME = "⌛"
FIRST = "<:_:1043226244575142018>"
SECOND = "<:_:1043226243463659540>"
THIRD = "<:_:1043226242335391794>"
STAR = "★"
EMPTY_STAR = "☆"


VERIFIED_COMPLETION = "<a:_:1406301513189425314>"
VERIFIED_FULL = "<a:_:1406302943266865223>"
VERIFIED_GOLD = "<a:_:1406302950443192320>"
VERIFIED_SILVER = "<a:_:1406302952263782466>"
VERIFIED_BRONZE = "<a:_:1406300035624341604>"
PENDING_COMPLETION = "<a:_:1406287846016422008>"
PENDING_FULL = "<a:_:1406287849183248446>"
PENDING_GOLD = "<a:_:1406287854417481819>"
PENDING_SILVER = "<a:_:1406287857705816188>"
PENDING_BRONZE = "<a:_:1406287843747172373>"
WR_FULL = "<a:_:1406287877020586056>"
WR_GOLD = "<a:_:1406287880762167456>"
WR_SILVER = "<a:_:1406287887850536991>"
WR_BRONZE = "<a:_:1406287874143551528>"
REJECTED = "<a:_:1406287841771651232>"


def placements() -> dict[Literal[1, 2, 3], str]:
    """Create a dictionary for easy access to placement emojis."""
    return {
        1: FIRST,
        2: SECOND,
        3: THIRD,
    }


def get_placement_emoji(placement: int) -> str:
    """Get the placement emoji."""
    if placement not in (1, 2, 3):
        return ""
    return placements()[placement]


def stars_rating_string(rating: float | None = None) -> str:
    """Create a star rating string."""
    if not rating:
        return "Unrated"
    filled = math.ceil(rating) * STAR
    return filled + ((6 - len(filled)) * EMPTY_STAR)


def generate_all_star_rating_strings() -> list[str]:
    """Generate all possible star combinations."""
    return [stars_rating_string(x) for x in range(1, 7)]
