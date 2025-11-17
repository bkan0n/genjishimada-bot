from typing import Any

from genjipk_sdk.change_requests import ChangeRequestResponse


class FormattableChangeRequest(ChangeRequestResponse):
    def to_format_dict(self) -> dict[str, Any]:
        """Convert the struct to a dictionary for rendering.

        Returns:
            dict[str, str | None]: Mapping of field names to values.
        """
        return {
            "Code": self.code,
            "Type": self.change_request_type,
            "Request": self.content,
        }


class FormattableStaleChangeRequest(ChangeRequestResponse):
    def to_format_dict(self) -> dict[str, Any]:
        """Convert the struct to a dictionary for rendering.

        Returns:
            dict[str, str | None]: Mapping of field names to values.
        """
        return {
            "Code": self.code,
            "Type": self.change_request_type,
            "Request": self.content,
        }
