# Reference-extraction & enrichment — feature backlog

Deferred feature ideas for the two pre-analysis stages of the skill scan:

- **refExtract stage** (`skillspector/refextract/`, the `refExtract` graph node) —
  *pure-static* extraction from the in-memory file map (`file_cache`). No network,
  no corpus. Every feature here is computable offline from the skill's own files.
- **Enrichment stage** (the `Enrichment` graph node) — everything that needs the
  *network* or an *external corpus*: DNS/HTTP probes, registry lookups, reputation,
  typosquat distance. These are the audit toolkit's slow probers.

Status: **none of the below is implemented yet.** Implemented today: URL, repo,
package, and domain extraction with a `Context` (file, line, form, file_type).
This file is the parking lot for the rest, so the ideas aren't lost.

---

## refExtract stage — additional static features

### Per-URL attributes
| Feature | Signal |
|---|---|
| `insecure` (http://) | downgrade / MITM |
| `embedded_credentials` (`user:pass@`) | leaked secret in source |
| `ip_literal` host | DNS-less / evasion |
| `nonstandard_port` | C2-ish endpoint |
| path file-extension (`.sh` / `.bin` / `.exe` / `.py`) | fetch-and-execute target type |
| `is_raw` (raw.githubusercontent / gist) | mutable code fetch |
| `is_shortener` (bit.ly / tinyurl / …) | obfuscated destination |
| `punycode` / IDN host | homograph spoofing |
| `pinned_ref` vs floating (40-hex SHA vs branch) | TOCTOU on fetched content |

### Per-repo attributes
- `ref` (branch / tag / commit) and `pinned?` (SHA vs floating)
- `is_gist`
- subpath within the repo
- cross-link to any `git clone` / `pip install git+…` command that uses it

### Per-package attributes
- version specifier + `pinned?`
- `scoped?` (npm `@scope/name`)
- `source`: registry vs `git+url` vs local path
- declared extras
- (current known limitation: inline `pip install foo==1.2.3` is dropped because the
  toolkit's inline name regex rejects the `==` pin — manifest paths handle versions.
  Hardening this is itself a backlog item.)

### MCP references — IMPLEMENTED
Done: `refextract/mcp.py` (`Mcp` product + `extract_mcps`). Parses `mcpServers`
objects from JSON config (incl. root `.mcp.json`, now un-skipped by build_context).
Captures name, command + args, transport (stdio/sse/http), remote url, env-var
names, and a command→package cross-link. Still open as a follow-up: declared tool
names / allowed-tools from SKILL frontmatter.

### Additional external reference kinds (NOT yet covered)
The current extractors only see `http(s)://` URLs, github.com http repos, packages
from a fixed manager list, and MCP config. These external edges still slip through —
all statically extractable in the refExtract stage. Ranked roughly by security value.

**New product kinds:**
| Kind | What / where | Why it matters |
|---|---|---|
| GitHub Actions | `uses: owner/action@ref` in `.github/workflows/*.yml` | third-party code in CI; `@main` vs pinned SHA = TOCTOU. Zero coverage today. |
| Non-HTTP URIs | `s3://`, `gs://`, `git://`, `git+ssh`, `ssh://`, `ftp(s)://`, `ws(s)://`, DB strings (`postgres://`, `redis://`, `mongodb://`, `amqp://`) | URL regex is `https?://`-only — object-store + DB + git endpoints are invisible. |
| Container images | `image:` keys in `docker-compose.yml` / k8s manifests; bare `ghcr.io`/`quay.io` refs | only caught today via `docker pull` / Dockerfile `FROM`. |
| Model refs | HuggingFace (`from_pretrained("org/model")`, `huggingface.co/org/model`), Ollama (`ollama pull/run model`) | fetched, executable-adjacent artifacts; distinct supply-chain class. |
| Git submodules | `.gitmodules` | external repo deps pinned by commit; not an http URL. |
| Instruction-file refs | links a skill makes to other local instruction/skill files (`@file`, `./other.md`) | cross-skill/instruction edges (the original deferred kind). |

**Extensions to existing products (cheaper than new kinds):**
- More package ecosystems in the Package extractor: Helm (`helm repo add` / chart refs),
  Terraform modules (`source =`), Maven/Gradle, NuGet, Composer, apt PPAs
  (`add-apt-repository`), conda channels.
- `git+ssh` / scp-style repos (`git@github.com:owner/repo`) — Repo extractor only sees
  github.com *http* URLs today.
- Protocol-relative / HTML `src` (`//cdn.x/...`, `<script src=>`) — only matched today
  when fully `http(s)://`.

### Implemented since this doc was written
- **Fetch-and-execute** — `refextract/fetchexec.py` (`FetchExecute` + `ExecPattern`):
  7 patterns (curl|sh, eval $(curl), bash <(curl), python -c $(curl), curl+chmod,
  source <(curl), PowerShell iex/iwr). Ported from `scan_fetch_and_execute.py`.
  (Follow-up: the curl-then-chmod pattern only matches when on a single line.)
- **Service-family host classification** — `host.service_family()` + `ServiceFamily`
  enum on `Url`/`Domain` (code_host / package_registry / object_store / docs_host /
  ai_vendor / paas), orthogonal to `takeover_platform`. From `find_external_refs.category()`.
- **Templated-ref flag** — `host.is_templated()` + `is_template` on `Url`/`Domain`
  (`<account>`, `{host}`, `${VAR}`, `[your-domain]`; IPv6 `[::1]` excluded). Kept, not
  dropped, so a later stage filters the noise in one check.
- **Skill provenance** — `Context.skill` + the `current_skill` ContextVar, set once via
  `extract_references(files, skill=…)`. Every occurrence is reverse-traceable to its
  origin skill when many skills are pooled into one store. The CLI emits a per-skill
  `{skill: products}` map (pooling/merging across skills is left to the caller).

### Skill-level profile (aggregate, descriptive — for dataset-wide statistics)
| Feature | Notes |
|---|---|
| `file_count`, `total_bytes` | trivial |
| `lines_of_code` total + per language | from file_cache |
| `main_language` (by LoC) | |
| file-type histogram / language count | dataset stats (uses `FileType`) |
| `has_executable_scripts` | already produced by build_context |
| manifests present (which managers) | |
| frontmatter completeness (name / description / triggers / permissions + counts) | |
| prose size (SKILL.md words / lines), code:prose ratio | |
| reference summary (counts per kind, distinct hosts / owners / domains) | derived from the feed |
| `secret_indicators` (API-key / token regex) | **high security value** |
| `obfuscation_indicators` (base64 blobs, `eval`, minified / very long lines) | high value |

### Needs git history (conditional)
Only available when `resolve_input` cloned a real repo — **not** for S3-markdown-only
external scans. Emit `None` when there is no `.git`:
- `author_count`, `commit_count`
- last-commit age, first-commit age

---

## Enrichment stage — network / corpus features

These consume the refExtract feed and probe the outside world. Ported behavior
lives in the skills.sh audit toolkit's probers.

### Domains
- DNS resolves / NXDOMAIN
- registrar availability (AVAILABLE / REGISTERED / REDEMPTION / RESERVED)
- parking redirects (Sedo / DomainRiviera / dan.com)
- cross-apex 3xx redirects
- reputation score (the toolkit's `score_domain_reputation.py` signals)

### Hosts
- HEAD + DNS probe (alive / 404 / NXDOMAIN / CONN_FAIL / 403)
- host reputation score

### GitHub repos / owners
- owner exists / DELETED / RENAMED / NO_REPOS
- stars, account age, repo count, followers, bio
- dead-owner → claimable (typosquat / takeover) detection
- owner-gone × active install/clone command (active attack surface)

### Packages
- exists on registry (npm / pypi)
- download counts + first-publish age
- typosquat distance to popular packages
- registry `repository.url` → dead-owner package takeover

### Design boundary
refExtract must stay offline and deterministic; anything requiring a socket or a
reference corpus belongs in Enrichment. Keep that split when implementing the above.
