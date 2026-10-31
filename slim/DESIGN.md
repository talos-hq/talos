# talos design

## Goal
A consulting-grade, fast, antivirus-friendly install of NousResearch Hermes Agent
that is pre-wired to OpenRouter + `deepseek/deepseek-v4.1-flash`, hides Nous
Portal/login/model surfaces, and brings the gateway up as part of startup.
Telegram bot provisioning comes in a later phase.

## Repos
- `talos-hq/talos` — Talos: `NousResearch/hermes-agent` + our fixes + the signed installer. `talos-hq/hermes-slim` keeps the fixes without the rebrand.
  - `upstream` branch: exact mirror of `NousResearch/hermes-agent@main`. Never edited.
  - `main` branch: `upstream` + our patches. All our code lives under `slim/`
    and `.github/workflows/slim-*.yml`; upstream files are touched only by small,
    clearly marked hooks to keep merges trivial.
- `hermes-slim/wheels` — wheels we build ourselves for packages lacking a usable
  wheel on one of our targets (e.g. `cryptography` on macOS x64). Published as
  GitHub Release assets; consumed via `--find-links`.

## Targets
| id            | runner            | python                      |
|---------------|-------------------|-----------------------------|
| win32-x64      | windows-latest              | python-build-standalone 3.14.9 |
| darwin-x64     | macos-15-intel              | python-build-standalone 3.14.9 |
| darwin-arm64   | macos-latest                | python-build-standalone 3.14.9 |
| linux-x64-musl | ubuntu-latest + alpine:3.22 | python-build-standalone 3.14.9 (musl) |

Python and uv downloads reuse upstream's sha256 pins in `pm/lock.json`.

## Install strategy: blessed bundle ("artifact cache")
Instead of running Hermes' installer (downloads uv, Python, runs pm, fetches
dozens of packages at install time — what AV products flag), CI produces one
self-contained archive per target:

```
talos-<ver>-<target>.{tar.zst|zip}
  python/          relocatable CPython 3.14 (python-build-standalone)
  app/             hermes source at the tagged commit
  site-packages    every dependency pre-installed from uv.lock (+ our wheels)
  bin/hermes(.cmd) launcher
  bin/talos-setup(.cmd)
  SHA256SUMS
```
Client install = download → verify sha256 → unpack → run setup. No network
package resolution, no compilers, no `curl | sh`.

How a bundle is built (`slim/build_bundle.py`, native on each target):
1. Unpack the pinned CPython.
2. `uv export --frozen` the lock for core + extras `telegram mcp ddgs sms`
   (`sms` = aiohttp, which the gateway's `/health` API server needs).
3. `uv pip install --no-deps --no-build --find-links <hermes-slim/wheels>`.
   `--no-build` is deliberate: if any package lacks a wheel for a target the
   build fails, and the fix is a wheel in hermes-slim/wheels, never a compile
   on the client.
4. Copy the Hermes source (minus desktop app, website, tests) to `app/`, add
   launchers that set `PYTHONPATH=app` and `TALOS=1`.

### Why macOS x64 broke upstream
`cryptography==50.0.2` (a core dependency) ships no macOS x86_64 wheel, so
Intel Macs compile it from source, which needs Rust and OpenSSL headers.
hermes-slim/wheels builds it once in CI against a static OpenSSL 3.5 LTS with
`MACOSX_DEPLOYMENT_TARGET=11.0`. Everything else in our closure has cp314
wheels for all four targets.

## Setup flow (`slim/setup` — `talos-setup`)
Interactive TUI by default; every field has a CLI flag/env var so CI runs fully
non-interactive (`--non-interactive` fails instead of prompting).

Fields (phase 1):
- first name, last name — trimmed, internal whitespace collapsed, validated
  (letters/digits/-/' only, 1–40 chars). Stored for later bot naming.
- OpenRouter API key — format check (`sk-or-`), optional live check
  (`GET /api/v1/key`), skippable with `--skip-online-checks`.
- model — fixed default `deepseek/deepseek-v4.1-flash`, overridable by flag.

Writes exactly what Hermes' OpenRouter wizard flow writes, through Hermes' own
`save_env_value` / `save_config`:
- `.env`: `OPENROUTER_API_KEY`, plus `API_SERVER_KEY` (random), `API_SERVER_HOST=127.0.0.1`,
  `API_SERVER_PORT` (default 8642) so the gateway serves `/health`.
- `config.yaml`: `model.{default,provider: openrouter,base_url,api_mode: chat_completions}`,
  `nous.guest: false`, `slim.{first_name,last_name}`.
Having a provider configured satisfies Hermes' first-run guard, so no wizard appears.

Then the gateway: `hermes gateway install --force --start-now --start-on-login`
(launchd `ai.hermes.gateway` / systemd user unit / Windows scheduled task) with
`HERMES_NONINTERACTIVE=1` when run with `--service` (the installers pass it), otherwise a detached
`hermes gateway run`. Setup succeeds only once `GET http://127.0.0.1:8642/health`
returns `{"status":"ok"}`. The gateway runs with zero messaging platforms until Telegram
is added.

Phase 2 (deferred): Telegram — user id, 4 bots, usernames
`hermes_<first>_<last>_<N>_bot` (N=1..4), tokens validated with `getMe`.

## Nous surfaces
Hidden, not deleted, when `TALOS=1` (set by the launchers). Two upstream
touch points, 5 lines total:
- `plugins/model-providers/nous/__init__.py`: `hidden=True`, which drops Nous from every
  provider picker (CLI, TUI, Telegram `/model`, dashboard) via `listed_canonical_providers()`.
- `hermes_cli/setup.py`: the "Quick Setup (Nous Portal)" first-run choice is removed.
`nous.guest: false` in config disables the Nous free-tier fallback.

## Upstream sync
`slim-upstream-sync.yml` (scheduled + manual): fast-forward `upstream` to
Nous main, then open a PR `upstream → main`; the bundle matrix runs on that PR.
Upstream's own workflows are disabled in this fork via the Actions API
(`gh workflow disable`), which survives merges without editing their files.

## Install entry points
- macOS / Linux: `curl -fsSL https://talosbot.ai/install.sh | sh`
  (or download and run it) — picks the target, verifies sha256, unpacks to `~/.talos/current`,
  links `hermes` into `~/.local/bin`, runs setup with `--service`.
- Windows: `slim/install.ps1` — same, into `%LOCALAPPDATA%\\talos`, adds `bin` to the user PATH.

## Signed binaries (Windows Smart App Control)
Smart App Control, on by default on fresh Windows 11 installs, refuses to run or load
any unsigned binary, and has no per-app exception: the only way past it is turning it
off for good. So the Windows bundle ships signed binaries only:
- The interpreter is python.org's CPython (the NuGet `python` package, same version as
  the other targets), which the Python Software Foundation signs. python-build-standalone's
  Windows build, which upstream Hermes (via uv) and earlier bundles used, is unsigned:
  that is what SAC blocked.
- Dependency wheels ship ~110 unsigned `.pyd`/`.dll` (pywin32, Pillow, lxml, cryptography,
  pydantic-core, aiohttp, ...). `build_bundle.py --stage-only` lists them in
  `unsigned.txt` (`slim/pe_signatures.py`); CI signs that list with our Azure Artifact
  Signing certificate profile, checks every `.exe/.dll/.pyd` with `Get-AuthenticodeSignature`,
  then `--package-only` zips it. A `v*` tag fails if anything is not validly signed.
- Repo secrets: `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`,
  `AZURE_SIGNING_ENDPOINT`, `AZURE_SIGNING_ACCOUNT`, `AZURE_SIGNING_PROFILE`.

## Known gaps
- macOS bundles are not Developer ID signed or notarized; install.sh clears the quarantine
  flag instead. Signing + notarization (Apple Developer ID) is the real fix.
- `TALOS=1` is also written to `~/.hermes/.env`, which Hermes loads before provider plugins, so
  service-started gateways hide Nous too.
