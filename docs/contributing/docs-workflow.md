# Working on the Docs

The documentation site is built with [MkDocs](https://www.mkdocs.org/) and the Material theme. Markdown files live in `docs/`, the navigation is defined in `mkdocs.yml`, and a GitHub Actions workflow publishes the rendered site to GitHub Pages.

## Local preview

1. Install the project dependencies with `uv sync --group dev` so MkDocs and the Material theme are available in your virtual environment.【F:pyproject.toml†L19-L28】
2. Start a live preview with `uv run mkdocs serve -a 0.0.0.0:8000` (or use `mkdocs serve` directly inside your environment).
3. Open <http://127.0.0.1:8000> to browse the docs with hot reload.

## Branching strategy

1. Create a topic branch from `main`.
2. Commit Markdown updates under `docs/` (and adjust `mkdocs.yml` when the navigation changes).
3. Push to GitHub and open a pull request targeting `main`.

## Publishing to GitHub Pages

1. Ensure the **Publish Docs (MkDocs → Pages)** workflow (`.github/workflows/publish-docs.yml`) is enabled. It runs on every push to `main`, builds the site with `mkdocs build --strict`, and uploads the output as a GitHub Pages artifact.【F:.github/workflows/publish-docs.yml†L1-L45】
2. GitHub Pages should be configured to serve from the `gh-pages` branch. The deploy job uses `actions/deploy-pages@v4` to publish the artifact from the same workflow run.【F:.github/workflows/publish-docs.yml†L46-L55】
3. The live site is hosted at <https://bkan0n.github.io/genjishimada-bot/> as configured in `mkdocs.yml`. If you need to validate a change before merging, run `uv run mkdocs build --strict` locally to catch broken links.【F:mkdocs.yml†L1-L19】

## Writing guidelines

- Keep content narrative and architecture-focused—link to source files when readers need deeper implementation details.
- Update tables and queue lists whenever new services or jobs are introduced so operational knowledge stays current.
- Store diagrams or other assets under `docs/assets/` and reference them from the relevant Markdown file.

Ping the maintainers in `#bot-dev` on Discord if you need a review or additional context.
