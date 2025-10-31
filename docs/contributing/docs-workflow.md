# Working on the Docs

This repository uses [MkDocs](https://www.mkdocs.org/) with the Material theme to generate GitHub Pages content from the `docs-site` branch. Follow the steps below to propose updates.

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

1. Create or check out the `docs-site` branch:
   ```bash
   git checkout -B docs-site
   ```
2. Commit Markdown and configuration changes under `docs/` and `mkdocs.yml`.
3. Push to GitHub and open a pull request targeting `docs-site`.

## Publishing to GitHub Pages

1. Configure a GitHub Actions workflow (see `.github/workflows/publish-docs.yml`) or run manually:
   ```bash
   mkdocs build --clean
   ```
2. Deploy the generated `site/` directory to the `gh-pages` branch. You can automate this with `mike deploy` or an actions workflow.
3. In repository settings, set GitHub Pages to use the `gh-pages` branch (root).

## Writing guidelines

- Keep content narrative and architecture-focused—avoid auto-generated API dumps.
- Link directly to source files on GitHub (for example, `https://github.com/bkan0n/genjishimada-bot/blob/main/path/to/file.py`) so readers can jump into the code.
- Add diagrams or tables where they improve clarity; store assets under `docs/assets/`.
- Document new services, queues, or embeds as soon as they ship so this guide remains current.

Questions? Ping the maintainers in the `#bot-dev` Discord channel for review or direction.
