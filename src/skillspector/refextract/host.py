# Marketplace addition (air/space) — NOT part of upstream NVIDIA SkillSpector.
# Licensed under Apache-2.0, consistent with the vendored SkillSpector source.

"""Host classification helpers shared by the URL and domain extractors.

Ported from the skills.sh supply-chain audit toolkit, where the logic was
validated across the full skills.sh corpus:
  * apex_domain        -> extract_apex_domains.py
  * takeover_platform  -> classify_url_context.py (TAKEOVER_PATTERNS)
"""

from __future__ import annotations

import re
from enum import StrEnum


class TakeoverPlatform(StrEnum):
    """Subdomain-takeover-class platform a host sits on, or NONE.

    A host on one of these platforms is only as trustworthy as whoever currently
    holds the sub-resource — the takeover risk. NONE means "not a takeover-class
    host" (a present, groupable category for dataset statistics, not null)."""

    NONE = "none"
    VERCEL = "Vercel"
    NETLIFY = "Netlify"
    HEROKU = "Heroku"
    CLOUDFLARE_PAGES = "Cloudflare Pages"
    CLOUDFLARE_R2 = "Cloudflare R2"
    AWS_S3 = "AWS S3"
    AZURE_BLOB = "Azure Blob"
    AZURE_APP_SERVICE = "Azure App Service"
    FLY_IO = "Fly.io"
    RAILWAY = "Railway"
    RENDER = "Render"
    SURGE = "Surge"
    GITBOOK = "GitBook"
    GCS = "GCS"


class ServiceFamily(StrEnum):
    """What kind of service a host belongs to — orthogonal to TakeoverPlatform.

    Where TakeoverPlatform answers "is this a takeover-class sub-resource", this
    answers "what kind of thing is it" (code host, package registry, AI vendor, …).
    NONE means "no recognized family"; the raw host is preserved on the product, so
    unrecognized hosts are never collapsed away.
    """

    NONE = "none"
    CODE_HOST = "code_host"  # github / gitlab / bitbucket / codeberg / gist / *.github.io
    PACKAGE_REGISTRY = "package_registry"  # pypi / npm / dockerhub / ghcr / crates / ...
    OBJECT_STORE = "object_store"  # s3 / gcs / azure blob / r2 / DO Spaces
    DOCS_HOST = "docs_host"  # readthedocs / gitbook / notion
    AI_VENDOR = "ai_vendor"  # anthropic / openai / huggingface / cohere
    PAAS = "paas"  # vercel / netlify / heroku / fly / render / railway / surge / azure app


# Multi-part public suffixes seen in the corpus — so apex() keeps the registrable
# label rather than collapsing e.g. foo.github.io to github.io.
_MULTI_PART_SUFFIXES = {
    "co.uk", "co.jp", "co.kr", "co.in", "co.za", "co.il", "co.id",
    "com.au", "com.br", "com.mx", "com.cn", "com.tw", "com.sg", "com.ar",
    "com.tr", "com.hk", "com.pk", "com.es", "com.pl", "com.ru",
    "github.io", "gitlab.io",
    "vercel.app", "netlify.app", "pages.dev", "workers.dev", "fly.dev",
    "up.railway.app", "readthedocs.io", "readthedocs.org", "streamlit.app",
    "glitch.me", "herokuapp.com", "r2.dev", "r2.cloudflarestorage.com",
    "blob.core.windows.net", "azurewebsites.net", "cloudfront.net",
    "s3.amazonaws.com", "web.app", "firebaseapp.com",
}

# Host-suffix -> takeover-class platform.
_TAKEOVER_PATTERNS = [
    (r"\.vercel\.app$", TakeoverPlatform.VERCEL),
    (r"\.netlify\.app$", TakeoverPlatform.NETLIFY),
    (r"\.herokuapp\.com$", TakeoverPlatform.HEROKU),
    (r"\.pages\.dev$", TakeoverPlatform.CLOUDFLARE_PAGES),
    (r"\.r2\.dev$", TakeoverPlatform.CLOUDFLARE_R2),
    (r"\.s3\.amazonaws\.com$", TakeoverPlatform.AWS_S3),
    (r"\.blob\.core\.windows\.net$", TakeoverPlatform.AZURE_BLOB),
    (r"\.azurewebsites\.net$", TakeoverPlatform.AZURE_APP_SERVICE),
    (r"\.fly\.dev$", TakeoverPlatform.FLY_IO),
    (r"\.up\.railway\.app$", TakeoverPlatform.RAILWAY),
    (r"\.railway\.app$", TakeoverPlatform.RAILWAY),
    (r"\.onrender\.com$", TakeoverPlatform.RENDER),
    (r"\.surge\.sh$", TakeoverPlatform.SURGE),
    (r"\.gitbook\.io$", TakeoverPlatform.GITBOOK),
    (r"storage\.googleapis\.com$", TakeoverPlatform.GCS),
]
_TAKEOVER_RX = [(re.compile(pattern), platform) for pattern, platform in _TAKEOVER_PATTERNS]


def is_internal_host(host: str) -> bool:
    """True for loopback / RFC-1918 / .local hosts that aren't externally meaningful."""
    return (
        not host
        or host in ("localhost", "127.0.0.1")
        or host.startswith("192.168.")
        or host.startswith("10.")
        or host.endswith(".local")
    )


def apex_domain(host: str) -> str:
    """Return the eTLD+1 apex for a host, honoring known multi-part suffixes."""
    host = host.lower().strip(".")
    if not host or host.count(".") < 1:
        return ""
    parts = host.split(".")
    tail3 = ".".join(parts[-3:]) if len(parts) >= 3 else ""
    tail2 = ".".join(parts[-2:])
    if tail3 in _MULTI_PART_SUFFIXES:
        return ".".join(parts[-4:]) if len(parts) >= 4 else tail3
    if tail2 in _MULTI_PART_SUFFIXES:
        return ".".join(parts[-3:]) if len(parts) >= 3 else tail2
    return tail2


def takeover_platform(host: str) -> TakeoverPlatform:
    """Return the takeover-class platform for a host, or NONE when none match."""
    for pattern, platform in _TAKEOVER_RX:
        if pattern.search(host):
            return platform
    return TakeoverPlatform.NONE


# Placeholder markers that make a URL/host a template rather than a real target:
# <account>, {host}/{{var}}, ${VAR}/$VAR, and an ellipsis. (`[…]` handled separately
# below so IPv6 literals like [::1] aren't flagged.)
_TEMPLATE_RE = re.compile(r"[<>{}]|\.\.\.|…|\$\{?\w")


def is_templated(value: str) -> bool:
    """True when a URL/host looks templated (a placeholder, not a real target).

    Templated refs are kept, not dropped — this flag lets a later stage filter
    them as noise with a single check.
    """
    if _TEMPLATE_RE.search(value):
        return True
    # A square bracket marks a placeholder ([your-domain]) unless it's an IPv6
    # literal (https://[::1]/...), where the char after '[' is a hex digit or ':'.
    bracket = value.find("[")
    return bracket != -1 and value[bracket + 1: bracket + 2] not in "0123456789abcdefABCDEF:"


def service_family(host: str) -> ServiceFamily:
    """Classify a host into a service family (orthogonal to takeover), or NONE."""
    host = (host or "").lower()
    if not host:
        return ServiceFamily.NONE
    if (
        host == "github.com" or host.endswith(".github.com")
        or "githubusercontent" in host or host.endswith(".github.io")
        or host == "gitlab.com" or host.endswith(".gitlab.com") or host.endswith(".gitlab.io")
        or host == "bitbucket.org" or host == "codeberg.org" or host.endswith(".sr.ht")
    ):
        return ServiceFamily.CODE_HOST
    if (
        host in ("pypi.org", "files.pythonhosted.org") or host.endswith(".pypi.org")
        or host in ("npmjs.com", "www.npmjs.com", "registry.npmjs.org")
        or host in ("hub.docker.com", "registry.hub.docker.com", "ghcr.io", "quay.io", "gcr.io")
        or host.endswith(".pkg.dev")
        or host in ("crates.io", "static.crates.io", "rubygems.org")
        or host in ("pkg.go.dev", "proxy.golang.org", "packagist.org")
        or host in ("repo1.maven.org", "search.maven.org")
        or host in ("nuget.org", "www.nuget.org", "api.nuget.org")
    ):
        return ServiceFamily.PACKAGE_REGISTRY
    if (
        host == "s3.amazonaws.com" or host.endswith(".s3.amazonaws.com") or ".s3." in host
        or host.endswith(".blob.core.windows.net")
        or host == "storage.googleapis.com" or host.endswith(".storage.googleapis.com")
        or host.endswith(".r2.dev") or host.endswith(".r2.cloudflarestorage.com")
        or ".digitaloceanspaces.com" in host
    ):
        return ServiceFamily.OBJECT_STORE
    if (
        host.endswith(".readthedocs.io") or host.endswith(".readthedocs.org")
        or host.endswith(".gitbook.io") or host.endswith(".gitbook.com")
        or host.endswith(".notion.site") or host.endswith(".notion.so")
    ):
        return ServiceFamily.DOCS_HOST
    if (
        host == "anthropic.com" or host.endswith(".anthropic.com")
        or host == "openai.com" or host.endswith(".openai.com")
        or host in ("huggingface.co", "hf.co") or host.endswith(".huggingface.co")
        or host.endswith(".cohere.com") or host.endswith(".cohere.ai")
    ):
        return ServiceFamily.AI_VENDOR
    if host.endswith((
        ".vercel.app", ".netlify.app", ".herokuapp.com", ".pages.dev", ".fly.dev",
        ".onrender.com", ".surge.sh", ".azurewebsites.net", ".railway.app", ".up.railway.app",
    )):
        return ServiceFamily.PAAS
    return ServiceFamily.NONE
