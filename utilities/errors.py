from __future__ import annotations

import contextlib
import logging
import os
import traceback
from typing import TYPE_CHECKING

import discord
import sentry_sdk
from discord import ButtonStyle, HTTPException, NotFound, TextStyle, app_commands, ui

from .base import BaseView

if TYPE_CHECKING:
    from utilities._types import GenjiItx

SENTRY_AUTH_TOKEN = os.getenv("SENTRY_AUTH_TOKEN", "")
SENTRY_FEEDBACK_URL = os.getenv("SENTRY_FEEDBACK_URL", "")

log = logging.getLogger(__name__)


class UserFacingError(app_commands.errors.AppCommandError): ...


class APIUnavailableError(Exception):
    pass


class APIHTTPError(Exception):
    def __init__(self, status: int, message: str | None, error: str | None, extra: dict | None) -> None:
        """Init API Error."""
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message
        self.error = error
        self.extra = extra


class ReportIssueModal(ui.Modal):
    feedback = ui.TextInput(
        label="Add more info",
        style=TextStyle.long,
        placeholder="Please include any additional context.\n\nWhat were you doing when this happened?",
    )

    def __init__(self, original_itx: GenjiItx) -> None:
        """Init."""
        self.original_itx = original_itx
        super().__init__(title="Report Issue")

    async def on_submit(self, itx: GenjiItx) -> None:
        """On submit."""
        await itx.response.send_message(
            "Thank you for your feedback. This has been logged :)",
            ephemeral=True,
        )
        await self.original_itx.delete_original_response()


class ReportIssueButton(ui.Button["ErrorView"]):
    view: "ErrorView"

    def __init__(self, *, label: str = "Report Issue", style: ButtonStyle = ButtonStyle.red) -> None:
        """Init."""
        super().__init__(label=label, style=style)

    async def callback(self, itx: GenjiItx) -> None:
        """Callback."""
        modal = ReportIssueModal(original_itx=itx)
        await itx.response.send_modal(modal)
        event_id = None
        with sentry_sdk.push_scope() as scope:
            scope.set_user({"id": str(self.view.exception_itx.user.id), "username": self.view.exception_itx.user.name})
            scope.set_tag(
                "command", self.view.exception_itx.command.name if self.view.exception_itx.command else "unknown"
            )
            if self.view.exception_itx.namespace:
                scope.set_context("Command Args", {"Args": dict(self.view.exception_itx.namespace.__dict__.items())})

        event_id = sentry_sdk.capture_exception(self.view.exc)
        await modal.wait()

        if modal.feedback.value is not None:
            data = {
                "name": f"{self.view.exception_itx.user.name} ({self.view.exception_itx.user.id})",
                "email": "genjishimada@bkan0n.com",
                "comments": modal.feedback.value,
            }
            if event_id is not None:
                data["event_id"] = event_id
            await itx.client.session.post(
                SENTRY_FEEDBACK_URL,
                headers={
                    "Authorization": f"Bearer {SENTRY_AUTH_TOKEN}",
                    "Content-Type": "application/json",
                },
                json=data,
            )


class ErrorView(BaseView):
    def __init__(
        self,
        sentry_event_id: str | None,
        exc: Exception,
        exception_itx: GenjiItx,
        *,
        unknown_error: bool = False,
        description: str = "None",
    ) -> None:
        """Init."""
        self.sentry_event_id = sentry_event_id
        self.exc = exc
        self.exception_itx = exception_itx
        self.description = description
        self.unknown_error = unknown_error
        self._report_issue_button = ReportIssueButton(
            label="Report Issue" if unknown_error else "Send Feedback",
            style=ButtonStyle.red if unknown_error else ButtonStyle.blurple,
        )
        super().__init__(timeout=180)

    def rebuild_components(self) -> None:
        """Rebuild view components."""
        self.clear_items()
        container = ui.Container(
            ui.Section(
                ui.TextDisplay("## Uh-oh! Something went wrong." if self.unknown_error else "## What happened?"),
                ui.TextDisplay(f">>> Details: {self.description}"),
                accessory=ui.Thumbnail(
                    media="http://bkan0n.com/assets/images/icons/error.png"
                    if self.unknown_error
                    else "https://bkan0n.com/assets/images/icons/warning.png"
                ),
            ),
            ui.Separator(),
            ui.TextDisplay(f"# {self._end_time_string}"),
            ui.Separator(),
            ui.Section(
                ui.TextDisplay(
                    "-# Let us know what led to this and what you expected — your feedback helps us fix it faster!"
                    if self.unknown_error
                    else "-# Think this was a mistake? Let us know what happened and what you were expecting."
                ),
                accessory=self._report_issue_button,
            ),
            accent_color=discord.Color.red() if self.unknown_error else discord.Color.yellow(),
        )
        self.add_item(container)


async def on_command_error(itx: GenjiItx, error: Exception) -> None:
    """Handle application command errors."""
    exception = getattr(error, "original", error)
    event_id = sentry_sdk.capture_exception(exception)
    if isinstance(exception, UserFacingError):
        view = ErrorView(event_id, exception, itx, description=str(exception))
        view.original_interaction = itx
    else:
        description = None
        if isinstance(exception, APIUnavailableError):
            description = "We are having trouble connecting to some backend services. Please try again later."
        view = ErrorView(event_id, exception, itx, description=description or "Unknown error.", unknown_error=True)
        view.original_interaction = itx

    log.debug(traceback.format_exception(None, exception, exception.__traceback__))

    with contextlib.suppress(HTTPException, NotFound):
        if itx.response.is_done():
            await itx.edit_original_response(content=None, view=view)  # type: ignore

        else:
            await itx.response.send_message(view=view, ephemeral=True)

    if not isinstance(exception, UserFacingError):
        raise exception
