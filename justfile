lint:
    ruff format .
    ruff check .
    basedpyright .

run:
    python main.py

updatesdk:
    uv remove genjipk-sdk
    uv add "genjipk-sdk @ git+https://github.com/bkan0n/genjipk-sdk" --upgrade


updatedpy:
    uv sync -P discord.py
