from logging import getLogger

from genjipk_sdk.models import CompletionCreateDTO, CompletionSubmissionReadDTO
from genjipk_sdk.models.completions import SuspiciousCompletionReadDTO
from genjipk_sdk.utilities._types import MedalType

from .emojis import (
    VERIFIED_BRONZE,
    VERIFIED_COMPLETION,
    VERIFIED_FULL,
    VERIFIED_GOLD,
    VERIFIED_SILVER,
    WR_BRONZE,
    WR_FULL,
    WR_GOLD,
    WR_SILVER,
)

log = getLogger(__name__)


class SuspiciousCompletionModel(SuspiciousCompletionReadDTO):
    def to_format_dict(self) -> dict[str, str | None]:
        """For use with Formatter."""
        return {
            "Type": self.flag_type,
            "Context": self.context,
            "Flagged by": f"<@{self.flagged_by}>",
        }


class CompletionSubmissionModel(CompletionSubmissionReadDTO):
    def to_format_dict(self) -> dict[str, str | None]:
        """For use with Formatter."""
        description = {
            "Code": self.code,
            "Map": self.map_name,
            "Time": self.time,
            "Difficulty": self.difficulty,
            "Video": f"[Link]({self.video})" if not self.completion and self.video else "",
            "Also Known As": self.also_known_as,
            "Hypothetical Rank": self.hypothetical_rank,
            "Hypothetical Medal": self.hypothetical_medal,
        }

        return description

    def get_verification_status_text(self) -> str:
        """Generate a celebratory status message based on verification, completion, and medal state.

        Returns:
            str: A concise, styled message describing the current verification status of the submission.
        """
        medal = self.hypothetical_medal.lower() if self.hypothetical_medal else None

        if self.completion:
            if self.verified:
                status = "Verified screenshot submission! A clear has been confirmed without video proof."
            else:
                status = "Pending verification. Screenshot submitted — awaiting review."
        elif self.verified and self.hypothetical_rank == 1:
            if medal:
                status = f"World Record! Fully verified with a {medal} medal."
            else:
                status = "World Record! Fully verified with no medal awarded."
        elif self.verified:
            if medal:
                status = f"Fully verified submission! Awarded a {medal} medal."
            else:
                status = "Fully verified submission! No medal awarded."
        elif medal:
            status = f"Pending verification. Qualifies for a {medal} medal."
        else:
            status = "Pending verification. Medal eligibility to be determined."

        return status


class CompletionPostVerificationModel(CompletionSubmissionModel):
    def to_format_dict(self) -> dict[str, str | None]:
        """For use with Formatter."""
        description = {
            "Code": self.code,
            "Map": self.map_name,
            "Time": self.time,
            "Difficulty": self.difficulty,
            "Video": f"[Link]({self.video})" if not self.completion and self.video else "",
            "Also Known As": self.also_known_as,
            "Rank": self.hypothetical_rank,
            "Medal": self.hypothetical_medal,
        }

        return description


class CompletionCreateModel(CompletionCreateDTO):
    def to_format_dict(self) -> dict[str, str | None]:
        """For use with Formatter."""
        description = {
            "Code": self.code,
            "Time": self.time,
            "Video": f"[Link]({self.video})" if self.video else "",
        }

        return description


_MEDAL_TO_VERIFIED = {
    "full": VERIFIED_FULL,
    "gold": VERIFIED_GOLD,
    "silver": VERIFIED_SILVER,
    "bronze": VERIFIED_BRONZE,
}
_MEDAL_TO_WR = {
    "full": WR_FULL,
    "gold": WR_GOLD,
    "silver": WR_SILVER,
    "bronze": WR_BRONZE,
}


def get_completion_icon_emoji(rank: int | None, medal: "MedalType | None") -> str:
    """Return the emoji for a completion/record based on rank and medal.

    Rules:
      - If ``rank is None`` → this is a plain completion → use VERIFIED_COMPLETION.
      - If ``rank == 1`` → world record icon (WR_*) keyed by medal (defaults to FULL).
      - Otherwise → verified icon (VERIFIED_*) keyed by medal (defaults to FULL).

    Args:
        rank: Placement rank; ``None`` means it's a completion (not a ranked record).
        medal: Medal category for the run (e.g., "gold", "silver", "bronze", or "full"). If ``None``,
            it is treated as "full".

    Returns:
        str: The Discord emoji string to display.

    Raises:
        ValueError: If ``medal`` is provided but not one of {"full","gold","silver","bronze"}.
    """
    if rank is None:
        return VERIFIED_COMPLETION

    key = "full" if medal is None else str(medal).lower()
    if key not in ("full", "gold", "silver", "bronze"):
        raise ValueError(f"Unknown medal type: {medal!r}")

    if rank == 1:
        return _MEDAL_TO_WR[key]

    return _MEDAL_TO_VERIFIED[key]


def get_completion_icon_url(completion: bool, verified: bool, rank: int | None, medal: MedalType | None) -> str:
    """Return the applicable icon url for this completion submission."""
    base_url = "https://bkan0n.com/assets/images/genji/verification"
    if completion:
        prefix = "verified" if verified else "pending"
        return f"{base_url}/{prefix}_completion.avif"

    _medal = medal.lower() if medal else "full"

    if verified and rank == 1:
        return f"{base_url}/wr_{_medal}.avif"

    prefix = "verified" if verified else "pending"
    return f"{base_url}/{prefix}_{_medal}.avif"


def make_ordinal(n: int) -> str:
    """Convert an integer into its ordinal representation.

    make_ordinal(0)   => '0th'
    make_ordinal(3)   => '3rd'
    make_ordinal(122) => '122nd'
    make_ordinal(213) => '213th'
    """
    n = int(n)
    suffix = ["th", "st", "nd", "rd", "th"][min(n % 10, 4)]
    if 11 <= (n % 100) <= 13:  # noqa: PLR2004
        suffix = "th"
    return str(n) + suffix
