# Working on the Docs

This repository uses [MkDocs](https://www.mkdocs.org/) with the Material theme to generate GitHub Pages content. The Markdown lives on the `main` branch and a GitHub Actions workflow publishes the rendered static site to the `gh-pages` branch that GitHub Pages serves. Follow the steps below to propose updates.

## Local preview

1. Install MkDocs and the Material theme:
   ```bash
   uv tool install mkdocs
   uv tool install mkdocs-material
   ```
   Or add them to a virtual environment with `pip install mkdocs mkdocs-material`.
2. Start the live preview:
   ```bash
   mkdocs serve
   ```
3. Open <http://127.0.0.1:8000> to browse the docs with hot reload.

## Branching strategy

1. Create a topic branch from `main`:
   ```bash
   git checkout -b docs/update-whatever
   ```
2. Commit Markdown and configuration changes under `docs/` and `mkdocs.yml`.
3. Push to GitHub and open a pull request targeting `main`.

## Publishing to GitHub Pages

1. Ensure the **Publish Docs** workflow (`.github/workflows/publish-docs.yml`) is enabled. It runs on every push to `main` and takes care of building the static site with `mkdocs build --strict`.
2. In repository settings, set GitHub Pages to serve from the `gh-pages` branch (root). The workflow pushes the latest `site/` output there using the built-in `GITHUB_TOKEN`, so no extra secrets are needed.
3. After the workflow finishes, the site appears at <https://bkan0n.github.io/genjishimada-bot/>. If you need to validate a change before merging, run `mkdocs build --strict` locally to catch broken links.

## Writing guidelines

- Keep content narrative and architecture-focused—avoid auto-generated API dumps.
- Link directly to source files on GitHub (for example, `https://github.com/bkan0n/genjishimada-bot/blob/main/path/to/file.py`) so readers can jump into the code.
- Add diagrams or tables where they improve clarity; store assets under `docs/assets/`.
- Document new services, queues, or embeds as soon as they ship so this guide remains current.

Questions? Ping the maintainers in the `#bot-dev` Discord channel for review or direction.
