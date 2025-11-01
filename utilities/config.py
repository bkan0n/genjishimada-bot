from __future__ import annotations

import msgspec

__all__ = ("Config", "decode")


class Base(msgspec.Struct, forbid_unknown_fields=True): ...


class Mentionable(Base):
    general_announcements: int
    website_patch_notes: int
    framework_patch_notes: int
    modmail: int


class Location(Base):
    south_america: int
    oceana: int
    north_america: int
    europe: int
    asia: int
    africa: int


class Platform(Base):
    console: int
    pc: int


class Misc(Base):
    tag_maker: int
    developer: int
    mapmaker: int


class Admin(Base):
    mod: int
    sensei: int
    dev: int
    joe: int


class Roles(Base):
    mentionable: Mentionable
    location: Location
    platform: Platform
    misc: Misc
    admin: Admin


class Updates(Base):
    announcements: int
    newsfeed: int
    xp: int
    editor_patch_notes: int
    website_patch_notes: int
    dlq_alerts: int


class Information(Base):
    rank_promotion: int
    map_submission_info: int
    website_info: int
    xp_info: int
    role_react: int


class Submission(Base):
    completions: int
    playtest: int
    verification_queue: int
    upvotes: int


class Help(Base):
    ask_for_help: int
    modmail: int
    suggestions_and_bug_reports: int
    change_requests: int


class AdminChannel(Base):
    development: int
    round_table: int


class Channels(Base):
    updates: Updates
    information: Information
    submission: Submission
    help: Help
    admin: AdminChannel


class Config(Base):
    guild: int
    roles: Roles
    channels: Channels


def decode(data: bytes | str) -> Config:
    """Decode a config.toml file."""
    return msgspec.toml.decode(data, type=Config)
