# refextract — review & validation guide

A runbook for reviewing `skillspector.refextract` (the reference-extraction package
behind the `refExtract` graph node). Written for a reviewer with **no prior context**.
Work top to bottom: orient → run it → design review → silent-drop review → bad-impl
review → cross-validate on sample data.

The package extracts every external reference a skill points at — URLs, apex domains,
GitHub-style repos, registry packages, MCP servers, and fetch-and-execute commands —
from a `{relative-path: file-content}` map. It is stdlib-only and reusable.

---

## 0. Guiding principles (the bar to hold the code to)

These were established deliberately; do **not** "fix" code back across them:

1. **Silent drops are unforgivable.** Data that enters and disappears with no trace is
   the worst defect. Over-capture (noise / false positives) is fine — a later stage
   filters. Compute is *not* a constraint.
2. **Capture, then classify — never gate.** "What kind of file/host/url is this" is
   *recorded as a field*, never used to skip extraction.
3. **Don't collapse anomalies.** Unknown file types keep their real extension (no
   `OTHER` bucket); unknown hosts keep their host string. Sentinels like
   `ServiceFamily.NONE` are fine *only* because the raw value is preserved elsewhere.
4. **Preserve multiplicity.** Deduped products (Domain/Repo/Package) keep a list of
   `Occurrence`s, never collapse to a single site.
5. **Flag noise, don't drop it.** Templated URLs (`https://<account>`) are kept with
   `is_template=True` so a later stage toggles them off in one check.
6. **Enums over magic strings** for closed taxonomies (RefForm, FileType-known-set,
   TakeoverPlatform, ServiceFamily, Transport, ExecPattern).
7. **Reusable:** `refextract` imports nothing from skillspector internals (stdlib only).
8. One deliberate exception to #2: `_STOPWORDS` in `package.py` drops common English
   words from greedy install-command captures (high-volume prose noise). Intentional.

---

## 1. Orientation — module map & data flow

```
paths.py        basename / suffix / is_markdown                       (leaf)
context.py      RefForm, FileType, Context(+skill), file_type_for, context_for,
                current_skill (ContextVar)                              → paths
host.py         apex_domain, host_category-free: service_family, takeover_platform,
                is_templated, is_internal_host; enums ServiceFamily/TakeoverPlatform (leaf)
markdown.py     fence_map, markdown_form, fetch-verb table              → context
occurrence.py   Occurrence(context, source_url)                         → context
sources.py      url_sources(*groups) over URL-bearing products          → context
url.py          Url + extract_urls(files) + first_url_on_line — the ONE
                URL recognizer+trimmer (fetchexec reuses it)            → context, host, markdown, paths
repo.py         Repo + derive_repos(urls, mcps)                         → url, mcp, occurrence, sources
domain.py       Domain + derive_domains(urls, mcps)                     → url, mcp, host, occurrence, sources
package.py      Package + extract_packages(files) + launcher_package +
                normalize_package_name (one home for "which package
                does this command reference"; mcp's cross-link uses it) → context, markdown, occurrence, paths
mcp.py          Mcp, Transport + extract_mcps(files); dedup by FULL
                identity, occurrences tuple (name collisions w/
                different payloads stay separate products)              → context, occurrence, package, paths
fetchexec.py    FetchExecute, ExecPattern + extract_fetch_executes(files) → context, markdown, paths, url
references.py   ExtractedReferences (the feed container)                → url/repo/domain/package/mcp/fetchexec
__init__.py     extract_references(files) + re-exports
__main__.py     CLI: python -m skillspector.refextract <dir>  (per-skill {skill: feed} map)
```

**Data flow** (`extract_references`): `extract_urls` and `extract_mcps` read files;
`derive_repos`/`derive_domains` are *derived* from every URL-bearing product (URLs +
MCP remote endpoints, via `sources.url_sources`); `extract_packages` and
`extract_fetch_executes` read files independently. Result is one `ExtractedReferences`.

DAG is acyclic; `url` is the hub (repos/domains derive from it). Verify no cycles if
you add a module.

---

## 2. Run it (sanity + smoke)

All commands assume cwd = this project root (`src/scanning-engine/skillspector/`), where
`uv run` finds the venv automatically (no hardcoded paths).

```bash
# lint + tests
uvx ruff@latest check src/skillspector/refextract/
.venv/bin/python -m pytest -q tests/unit/test_refextract.py   # dedicated regression suite (every past bug)
.venv/bin/python -m pytest -q tests/ -k "refextract or url or domain or repo or package or mcp or graph or build_context or node or pipeline or context or host"

# run the extractor on any skills dir → JSON. A dir of skill subdirs yields a
# {skill: products} map (per-skill, not merged); a single-skill dir yields a bare feed.
uv run python -m skillspector.refextract <SKILLS_DIR>
uv run python -m skillspector.refextract <SKILLS_DIR> | jq '.[].fetch_executes'      # over all skills
uv run python -m skillspector.refextract <SKILLS_DIR> | jq '[.[].urls[] | select(.is_template|not)] | length'
```

**Known-noise:** two tests fail and are unrelated to refextract —
`test_llm_analyzer_base.py::{TestLLMAnalysisResult,TestMetaAnalyzerResult}::test_confidence_validation`
(a pydantic bound-check, pre-existing). Everything else should pass (~349).

---

## 3. Design review

Check, per axis:

- **Data flow:** Is anything re-derived inconsistently across modules? (We accept
  re-parsing files multiple times — perf is not a constraint — but the *logic* must be
  shared, e.g. `markdown.markdown_form` is the single form classifier; `sources.url_sources`
  is the single URL-bearing enumerator. Watch for a second copy drifting.)
- **Data structures:** Every product is a frozen dataclass with `to_dict()`. Deduped
  products carry `occurrences: tuple[Occurrence, ...]`. Closed taxonomies are enums.
  `ExtractedReferences` fields are tuples (truly immutable). Cross-links are by-value
  strings (e.g. `Mcp.package` matches `Package.slug`), not object refs — intentional.
- **Skill provenance:** `Context.skill` records the origin skill so a pooled multi-skill
  store stays reverse-inspectable. It is *not* threaded through signatures — the caller
  passes it once (`extract_references(files, skill=…)`), which sets the `current_skill`
  ContextVar that `context_for` reads. Default None = skill-agnostic. Watch that no
  extractor re-introduces a `skill` parameter (it shouldn't need one).
- **Function signatures:** `extract_*` read files (`Mapping[str,str]`); `derive_*` take
  already-extracted products. Internal occurrence iterators yield small NamedTuples
  (e.g. `_UrlHit`) not bare tuples.
- **File separation:** one product per module + shared helpers (host/markdown/context/
  paths/sources/occurrence). A new product kind touches ~6 sites (references fields +
  counts + to_dict, __init__ import/__all__/extract_references) — this repetition is
  **accepted on purpose** (reading clarity > fewer edits); do not introduce a registry
  abstraction to "fix" it.

---

## 4. Silent-drop / gate review (the highest-value pass)

**Method:** read every extractor and interrogate each `continue`, filter, dedup,
slice, regex char-class, and `except`. For each, ask: *does data enter and leave no
trace?* Write a tiny repro for anything suspicious (see §5 method).

**Method 2 — empirical differential (cheap, catches what code-reading misses):**
grep the corpus for a *looser* shape than the recognizer, then diff counts against
extracted output. No ground truth needed — the corpus is its own oracle. Found 80+
dropped scheme refs (bare `s3://` mentions, `gs://`, `abfs://`) that two
code-reading passes had missed:

```bash
SUB=~/Documents/skills-sh-audit-toolkit/subset300/skills-md
uv run python -m skillspector.refextract "$SUB" > /tmp/refx.json
grep -rhoE "\b[a-z][a-z0-9+.-]{1,15}://" "$SUB" | sort | uniq -c | sort -rn | awk '$1>=3' > /tmp/raw.txt
jq -r '.[].urls[].scheme // empty' /tmp/refx.json | sed 's|$|://|' | sort | uniq -c > /tmp/got.txt
join -j2 -a1 <(sort -k2 /tmp/raw.txt) <(sort -k2 /tmp/got.txt) | awk '{ if (NF==2 || $2 > $3*1.5) print }'
# anything printed: raw mentions with no (or far fewer) extracted counterparts — investigate each
```

The same trick generalizes: grep loose for install verbs vs extracted packages,
`mcpServers` vs extracted mcps, fetch-verb + pipe shapes vs fetch_executes. Run it
whenever a pattern list (recognizer, manifest table, fetchexec patterns) is extended
— every silent drop found so far lived in one of those.

Patterns that have bitten this code before — check they have not regressed or
reappeared in new code:

- **Allowlist gates on file type.** Extraction must run on *every* file; the file's
  nature goes in `Context.file_type`. (Regression test: a URL/package in a `.cfg`,
  `.ipynb`, `LICENSE`, or no-extension file must still be extracted.)
- **`[:N]` slices** that keep only the first match (e.g. only the first package of
  `pip install a b c`). Must keep all.
- **Narrow regex captures that stop at the first flag.** Install-command captures must
  grab the whole arg list up to a shell separator (`[^|&;<>``]+`) and let `_clean_inline`
  drop flags — not stop at the first `-flag`.
- **Dedup that collapses occurrences.** Deduped products must accumulate `Occurrence`s.
- **Exact-filename matching** that misses variants/lockfiles (`requirements-dev.txt`,
  `package-lock.json`, `poetry.lock`, …). Use `_manifest_handler`'s pattern matching.
- **Regex char-classes that truncate.** `_URL_RE` must keep `<`/`(`/`)`/`[` (templated
  refs, parenthesized paths) and only stop at `>`/`]`/quotes/space — else placeholder
  and Wikipedia-style URLs vanish.
- **Crash-on-malformed → whole-run failure.** JSON parsers must guard
  `isinstance(parsed, dict)` before `.get`.
- **Value normalizers that reject instead of normalize** (e.g. rejecting `requests==2.x`
  for containing `=`). Normalize (strip version/extras), keep VCS refs whole.

**Known UNFIXED silent drops** are catalogued in the backlog's
"KNOWN SILENT DROPS" section (YAML mcpServers configs, MCP shape shorthand,
missing manifest parsers, recognizer scope) — check there before hunting, and
extend that list (not just this guide) when you find a new one you don't fix.

**Deliberate gates — do NOT "fix" these** (they are intended, noise-reduction or
out-of-scope, tracked in `refextract-enrichment-feature-backlog.md`):
- ~~Non-HTTP URI schemes~~ — implemented Jun 2026: `_URL_RE` captures any scheme,
  protocol-relative `//host`, and bare `www.` (host fallback in `_build_url`).
- Plain `http(s)://…whl` *install targets* skipped in `package._package_name` (the URL
  is still caught by `extract_urls`).
- Internal hosts (`localhost`/RFC-1918/`.local`) excluded from Domain derivation (the
  Url is still kept).
- `_STOPWORDS` prose filter in `package.py`.
- MCP env *values* dropped (names kept) — security, correct.

---

## 5. Bad-implementation (parsing-correctness) review

**Method that works:** import the private parser/helper directly and feed adversarial
inputs; print actual output; only claim a bug after a repro. Example:

```bash
.venv/bin/python - <<'PY'
from skillspector.refextract.package import _iter_packages, _parse_requirements_txt, launcher_package, split_package_token
from skillspector.refextract.repo import _forge_repo
from skillspector.refextract.url import extract_urls, _trim_url
pk = lambda t: [f"{hit.manager}:{hit.name}" for hit in _iter_packages("x.sh", t)]
print(pk("pip install -U --quiet requests flask"))          # all pkgs, flags after arg
print(pk("npx -y create-react-app"))                        # single-dash flag skipped
print(pk("docker run --gpus all --rm -e KEY img:tag cmd"))  # -> docker:img (canonical; tag is the occurrence version)
print(split_package_token("uvicorn[standard]==0.30"))       # -> ('uvicorn', '==0.30')
print(_parse_requirements_txt("-e git+https://h/r.git#egg=foo"))  # -> entry named 'foo', not 'git'
print(launcher_package("npx", ("-y","@scope/pkg@1.2")))           # -> npm:@scope/pkg (normalized = Package.slug)
print(_forge_repo("https://gitlab.com/grp/sub/proj"))             # nested group -> sub/proj
print([u.url for u in extract_urls({"f.md":"https://en.wikipedia.org/wiki/X_(Y)"})])  # parens kept
print(_trim_url("https://x.com)**"))                              # post-wrapper junk cut -> https://x.com
print(_trim_url("https://..."))                                   # placeholder kept (is_template), not bare scheme
PY
```

Specific things to re-verify (all were bugs once):
- One URL recognizer: `url.first_url_on_line` is the only line-level URL finder
  (fetchexec must NOT have its own regex/trim — it drifted once: paren URLs truncated).
- Names canonical, pins preserved: `split_package_token` returns (version-free name,
  verbatim version); the version lands on each `Occurrence.version` — NOT in the name
  (`docker:nginx:1.25` as a slug was a bug; so was dropping `==2.31.0` entirely).
- Inline install blobs must not stop at `>=`/`<=` (shell-redirect guard once truncated
  `pip install rich>=13.0 ccxt>=4.2 …` at the first `>`, dropping every later package).
- Every manifest parser returns `_ManifestEntry` (name, version, manager) — one
  contract, no per-parser shapes, no isinstance sniffing.
- `_trim_url` cuts at the first *unbalanced* closing bracket (markdown `](url)**`,
  `)。`, `)|` junk) but keeps balanced ones; a pure placeholder (`https://...`)
  keeps its marker instead of degrading to a bare unflagged `https://`.
- MCP dedup is by full identity with an `occurrences` tuple — two same-name servers
  with different commands are BOTH kept (a shadowed redefinition is a signal, not a dupe).
- `Mcp.package` slug uses `package.normalize_package_name` — `npx pkg@1.2` must
  yield `npm:pkg`, joinable against `Package.slug` (it once kept the raw `@1.2`).
- One docker-arg grammar: `package._docker_image` (value-flags incl. `--gpus`) serves
  both inline installs and the MCP cross-link (there were two divergent parsers).
- `url_sources` skips a later group's (file, url) already yielded by an earlier group
  (an MCP endpoint otherwise double-counts: once as Url, once as Mcp).
- CLI: a single skill *containing subdirs* is one skill (any direct file ⇒ single);
  only a dir of only-subdirs is a multi-skill root (root files were once dropped).
- Multi-package installs keep every package; versions/extras stripped (`requests==2.x`→
  `requests`, `foo[extra]`→`foo`, `@scope/p@1`→`@scope/p`); `git+`/VCS kept whole.
- `npx`/`go` skip single-dash flags (`npx -y pkg`).
- docker cross-link skips flag *values* (`-e KEY img` → image, not `KEY`).
- requirements VCS/editable lines use `#egg=` / keep the ref, never emit `"git"`/`"."`.
- GitLab nested groups keep the full project path; GitHub/gist/bitbucket are 2-segment.
- JSON parsers don't crash on a non-dict top level.
- Lockfiles parse (`package-lock.json`, `poetry.lock`/`Cargo.lock`, `Pipfile.lock`,
  `go.sum`, `yarn.lock` (skip `__metadata`), `pnpm-lock.yaml`).
- `is_templated` true for `<…>`/`{…}`/`${…}`/`[your-domain]`, false for IPv6 `[::1]`.
- `markdown_form` / `fence_map` are the only fence/form logic (no second copy).

---

## 6. Cross-validate on real sample data

There is a labelled corpus from the upstream audit toolkit (the source of this logic).
On this machine: `~/Documents/skills-sh-audit-toolkit/subset300` — 454 skills under
`skills-md/`, plus the toolkit's own extraction outputs as **ground truth**
(`url_context.tsv`, `external_refs.tsv`, `packages_refs.tsv`).

The CLI emits a per-skill `{skill: products}` map, so the jq below flattens with `.[]`
(occurrence-level counts are stable; deduped products — domains/repos/packages — are
per-skill here, so they sum *higher* than a globally-deduped feed would).

```bash
SUB=~/Documents/skills-sh-audit-toolkit/subset300
uv run python -m skillspector.refextract "$SUB/skills-md" > /tmp/refx.json

# our counts (summed across the per-skill map)
jq '{urls:([.[].urls[]]|length), repos:([.[].repos[]]|length),
     packages:([.[].packages[]]|length), domains:([.[].domains[]]|length),
     mcps:([.[].mcps[]]|length), fetch:([.[].fetch_executes[]]|length),
     templated:([.[].urls[]|select(.is_template)]|length)}' /tmp/refx.json

# toolkit ground truth
echo "tk url rows:  $(($(wc -l < "$SUB/url_context.tsv")-1))"
echo "tk pkg distinct: $(tail -n+2 "$SUB/packages_refs.tsv"|awk -F'\t' '{print $2\":\"$3}'|sort -u|wc -l)"

# diff distinct URLs; classify theirs-not-ours as real-drop vs normalization
tail -n+2 "$SUB/url_context.tsv"|cut -f4|sort -u > /tmp/tk.txt
jq -r '.[].urls[].url' /tmp/refx.json|sort -u > /tmp/ours.txt
comm -23 /tmp/tk.txt /tmp/ours.txt | head -40    # inspect: real drops, or longer/cleaner in ours?
```

**Expected (Jun 2026 baseline, subset300/skills-md — drift means investigate):**
ours ≈ `urls 13,989 occurrences (≈303 of them bare scheme-mention capability
signals, host=None) · fetch_executes 37 · templated 523`; distinct
URLs ≈ 6,041 (tk distinct 5,853 — ours is higher since the recognizer now also
captures non-HTTP schemes, protocol-relative, and bare-www refs the toolkit never
saw). Toolkit ≈ `13,509 url rows · 372 distinct packages`. Per-skill sums ≈
`repos 1,538 · packages 1,186 · domains 2,872`; package occurrences carrying a
verbatim version pin ≈ 494. `theirs-not-ours` ≈ 27: 25 classify as
more-complete/normalized per the rule below, and 2 are `git+https://…` refs where
the toolkit kept the bare `https://` tail but ours keeps the full VCS form
(host/apex/repo/domain still derive correctly from it — verified).
We should be **≥** the toolkit on every dimension. (Domain/repo/package *sums* over the
per-skill map exceed globally-deduped figures because they aren't merged across skills
here; that's expected, not a regression.) Every `theirs-not-ours` URL must classify
as (a) ours has the *more complete* form (toolkit truncated at `(`/`?`; or kept only
the `https://` tail of a `git+https://` ref), or (b) trailing-punct normalization
where ours is cleaner (`}`/`}}`/`?` junk the toolkit kept) —
**not** a genuine missing reference. A genuine miss is a silent drop: fix per §0.1.
(Numbers predating the Jun 2026 `_trim_url` fix: templated 436, distinct ≈5,941 — the
deltas were dirty markdown-wrapper variants merging into clean forms, plus placeholder
`https://...` URLs now kept flagged instead of degraded to bare `https://`.)

---

## 7. Related docs
- `refextract-enrichment-feature-backlog.md` — deferred extraction ideas + the
  refExtract(static) vs Enrichment(network/corpus) boundary, and the intended gates.
- Upstream logic source: the audit toolkit's `findings/scripts/` (`classify_url_context.py`,
  `extract_packages.py`, `find_external_refs.py`, `extract_apex_domains.py`,
  `scan_fetch_and_execute.py`).
