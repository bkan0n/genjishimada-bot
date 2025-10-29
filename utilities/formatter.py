from abc import ABC, abstractmethod
from typing import Protocol, Sequence


class FormattableProtocol(Protocol):
    def to_format_dict(self) -> dict[str, str | None]:
        """Return a dict for use with Formatter."""
        ...


class FormatterABC(ABC):
    @abstractmethod
    def __init__(self, model: FormattableProtocol, *args, **kwargs) -> None:
        """Initialize the formatter."""

    @abstractmethod
    def format(self) -> str:
        """Format an object."""


class FilteredFormatter(FormatterABC):
    def __init__(
        self,
        model: FormattableProtocol,
        *,
        value_wrap_character: str = "`",
        filter_fields: Sequence[str] | None = None,
    ) -> None:
        """Initialize the formatter.

        Args:
            model: A model that implements FormattableProtocol.
            value_wrap_character: Character used to wrap values.
            filter_fields: Fields to filter out of the foramtter.
        """
        self.values = model.to_format_dict()
        self._value_wrap_character = value_wrap_character
        self.filter_fields = filter_fields or set()

    def _wrap_str_code_block(self, value: str) -> str:
        return f"{self._value_wrap_character}{value}{self._value_wrap_character}"

    def format(self) -> str:
        """Format a Formattable model.

        This is used for Discord embed beautification.

        Returns:
            str: The formatted string.
        """
        res = ""
        filtered_values = {
            k: v
            for k, v in self.values.items()
            if v is not False and v is not None and v != "" and k not in self.filter_fields
        }
        for i, (name, value) in enumerate(filtered_values.items()):
            wrapped_name = self._wrap_str_code_block(name)
            res += f"> {wrapped_name} {value}\n"
        return res


class Formatter(FormatterABC):
    def __init__(
        self,
        model: FormattableProtocol,
        *,
        primary_character: str = "┣",
        secondary_character: str = "┗",
        value_wrap_character: str = "`",
    ) -> None:
        """Initialize the formatter.

        Args:
            model: A model that implements FormattableProtocol.
            primary_character: The primary character to use for each line.
            secondary_character: The secondary character to use for each line.
            value_wrap_character: Character used to wrap values.
        """
        self.values = model.to_format_dict()
        self._primary_character = primary_character
        self._secondary_character = secondary_character
        self._value_wrap_character = value_wrap_character

    def _wrap_str_code_block(self, value: str) -> str:
        return f"{self._value_wrap_character}{value}{self._value_wrap_character}"

    def _formatting_character(self, use_primary_character: bool) -> str:
        if use_primary_character:
            return self._primary_character
        return self._secondary_character

    def format(self) -> str:
        """Format a Formattable model.

        This is used for Discord embed beautification.

        Returns:
            str: The formatted string.
        """
        res = ""
        filtered_values = {k: v for k, v in self.values.items() if v is not False and v is not None and v != ""}
        length = len(filtered_values)
        for i, (name, value) in enumerate(filtered_values.items()):
            char = self._formatting_character(i + 1 < length)
            wrapped_name = self._wrap_str_code_block(name)
            res += f"{char} {wrapped_name} {value}\n"
        return res
