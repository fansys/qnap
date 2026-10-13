#!/usr/bin/env python3
"""CLI for Emby / Tailscale sync and QNAP feed generation.

Subcommands (used by parallel GitHub Actions jobs):

- emby      publish missing Emby releases
- tailscale publish missing Tailscale releases
- feeds     regenerate repo.xml / repo_pre.xml (single writer)
- all       local convenience: emby → tailscale → feeds
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from ci.emby import sync_emby  # noqa: E402
from ci.github_client import default_repo  # noqa: E402
from ci.http_util import HttpError  # noqa: E402
from ci.logutil import StepTimer, log, log_kv  # noqa: E402
from ci.paths import (  # noqa: E402
    DEFAULT_CERT,
    DEFAULT_KEY,
    SIGNATURES_EMBY_FRAGMENT,
    SIGNATURES_PATH,
    SIGNATURES_TAILSCALE_FRAGMENT,
    ensure_out_dirs,
)
from ci.repo_feed import write_repo_feeds  # noqa: E402
from ci.signatures import (  # noqa: E402
    load_signature_cache,
    merge_signature_caches,
    save_signature_cache,
)
from ci.tailscale import sync_tailscale  # noqa: E402
from ci.upstream import GithubError  # noqa: E402


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", default=default_repo(), help="target owner/name repository")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--ref", default=os.environ.get("GITHUB_REF_NAME") or "master")
    parser.add_argument(
        "--base-url",
        help="alias of --accelerate-prefix (GitHub mirror root for feed URLs)",
    )
    parser.add_argument(
        "--accelerate-preset",
        default="env",
        help="feed URL accelerate preset: env (default, read FEED_ACCELERATE_*), none, ghproxy, ...",
    )
    parser.add_argument(
        "--accelerate-prefix",
        help="feed URL accelerate prefix, e.g. https://ghproxy.net/",
    )
    parser.add_argument("--openssl", default=shutil.which("openssl") or "openssl")


def build_parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Sync Emby/Tailscale QNAP packages and write App Center feeds"
    )
    sub = root.add_subparsers(dest="command", required=True)

    emby = sub.add_parser("emby", help="sync Emby patched packages only")
    _add_common(emby)
    emby.add_argument("--version", help="process only this Emby upstream version")
    emby.add_argument("--allow-prerelease", action="store_true")
    emby.add_argument(
        "--force",
        action="store_true",
        help="rebuild and re-upload even if release tag already exists",
    )
    emby.add_argument("--sign-cert", type=Path, default=DEFAULT_CERT)
    emby.add_argument("--sign-key", type=Path, default=DEFAULT_KEY)
    emby.add_argument("--seven-zip", default=shutil.which("7z") or "7z")
    emby.add_argument(
        "--signatures-out",
        type=Path,
        default=SIGNATURES_EMBY_FRAGMENT,
        help="write signature fragment JSON for the feeds job",
    )

    tailscale = sub.add_parser("tailscale", help="mirror Tailscale stable QPKGs only")
    _add_common(tailscale)
    tailscale.add_argument(
        "--version",
        help="Tailscale product version (e.g. 1.104.1); empty = current stable",
    )
    tailscale.add_argument(
        "--force",
        action="store_true",
        help="re-upload assets even if release tag already exists",
    )
    tailscale.add_argument(
        "--signatures-out",
        type=Path,
        default=SIGNATURES_TAILSCALE_FRAGMENT,
        help="write signature fragment JSON for the feeds job",
    )

    feeds = sub.add_parser("feeds", help="regenerate repo.xml / repo_pre.xml")
    _add_common(feeds)
    feeds.add_argument(
        "--signatures-in",
        type=Path,
        nargs="*",
        default=[],
        help="optional signature fragment JSON files to merge",
    )

    all_cmd = sub.add_parser("all", help="run emby, tailscale, then feeds (local use)")
    _add_common(all_cmd)
    all_cmd.add_argument("--version", help="process only this Emby upstream version")
    all_cmd.add_argument("--allow-prerelease", action="store_true")
    all_cmd.add_argument("--sign-cert", type=Path, default=DEFAULT_CERT)
    all_cmd.add_argument("--sign-key", type=Path, default=DEFAULT_KEY)
    all_cmd.add_argument("--seven-zip", default=shutil.which("7z") or "7z")
    all_cmd.add_argument("--skip-emby", action="store_true")
    all_cmd.add_argument("--skip-tailscale", action="store_true")
    all_cmd.add_argument(
        "--force",
        action="store_true",
        help="force Emby and Tailscale release asset refresh",
    )
    all_cmd.add_argument(
        "--tailscale-version",
        help="Tailscale product version for all/tailscale (empty = current stable)",
    )
    return root


def _require_repo(repo: str) -> None:
    if not repo:
        raise SystemExit("target repository is required (--repo or GITHUB_REPOSITORY)")


def _run_emby(args: argparse.Namespace) -> int:
    _require_repo(args.repo)
    ensure_out_dirs()
    log("cli", "command=emby")
    if not args.dry_run:
        certificate = args.sign_cert.expanduser().resolve()
        private_key = args.sign_key.expanduser().resolve()
        if not certificate.is_file() or not private_key.is_file():
            raise SystemExit(f"signing material missing: {certificate} / {private_key}")
        if not shutil.which(args.seven_zip) and not Path(args.seven_zip).is_file():
            raise SystemExit(f"7z executable not found: {args.seven_zip}")
    else:
        certificate = args.sign_cert
        private_key = args.sign_key
    with StepTimer("cli", "emby sync"):
        sync_emby(
            args.repo,
            certificate=certificate,
            private_key=private_key,
            seven_zip=args.seven_zip,
            openssl=args.openssl,
            version=args.version,
            allow_prerelease=args.allow_prerelease,
            dry_run=args.dry_run,
            force=getattr(args, "force", False),
            signatures_out=args.signatures_out,
        )
    return 0


def _run_tailscale(args: argparse.Namespace) -> int:
    _require_repo(args.repo)
    ensure_out_dirs()
    log("cli", "command=tailscale")
    with StepTimer("cli", "tailscale sync"):
        sync_tailscale(
            args.repo,
            openssl=args.openssl,
            version=getattr(args, "version", None),
            dry_run=args.dry_run,
            signatures_out=args.signatures_out,
            force=getattr(args, "force", False),
        )
    return 0


def _run_feeds(args: argparse.Namespace) -> int:
    _require_repo(args.repo)
    log("cli", "command=feeds")
    if args.dry_run:
        log("feeds", "dry-run skipping repo.xml / repo_pre.xml write")
        return 0
    ensure_out_dirs()
    fragment_paths = [path for path in args.signatures_in if path.is_file()]
    for default_path in (SIGNATURES_EMBY_FRAGMENT, SIGNATURES_TAILSCALE_FRAGMENT):
        if default_path.is_file() and default_path not in args.signatures_in:
            fragment_paths.append(default_path)
    log_kv(
        "feeds",
        "signature inputs",
        {
            "base": SIGNATURES_PATH if SIGNATURES_PATH.is_file() else "-",
            "fragments": ",".join(str(path) for path in fragment_paths) or "-",
        },
    )
    fragments = [load_signature_cache(path) for path in fragment_paths]
    signatures = merge_signature_caches(load_signature_cache(SIGNATURES_PATH), *fragments)
    log("feeds", f"merged signature keys={len(signatures)}")
    with StepTimer("cli", "write feeds"):
        write_repo_feeds(
            args.repo,
            ref=args.ref,
            base_url=args.base_url,
            accelerate_preset=getattr(args, "accelerate_preset", None),
            accelerate_prefix=getattr(args, "accelerate_prefix", None),
            signature_by_version=signatures,
            openssl=args.openssl,
        )
    save_signature_cache(signatures, SIGNATURES_PATH)
    return 0


def _run_all(args: argparse.Namespace) -> int:
    log_kv(
        "cli",
        "command=all",
        {
            "repo": args.repo,
            "skip_emby": args.skip_emby,
            "skip_tailscale": args.skip_tailscale,
            "force": args.force,
            "dry_run": args.dry_run,
        },
    )
    shared = {
        "repo": args.repo,
        "dry_run": args.dry_run,
        "ref": args.ref,
        "base_url": args.base_url,
        "accelerate_preset": args.accelerate_preset,
        "accelerate_prefix": args.accelerate_prefix,
        "openssl": args.openssl,
    }
    if not args.skip_emby:
        _run_emby(
            argparse.Namespace(
                **shared,
                version=args.version,
                allow_prerelease=args.allow_prerelease,
                force=args.force,
                sign_cert=args.sign_cert,
                sign_key=args.sign_key,
                seven_zip=args.seven_zip,
                signatures_out=SIGNATURES_EMBY_FRAGMENT,
            )
        )
    if not args.skip_tailscale:
        _run_tailscale(
            argparse.Namespace(
                **shared,
                version=args.tailscale_version,
                force=args.force,
                signatures_out=SIGNATURES_TAILSCALE_FRAGMENT,
            )
        )
    return _run_feeds(
        argparse.Namespace(
            **shared,
            signatures_in=[SIGNATURES_EMBY_FRAGMENT, SIGNATURES_TAILSCALE_FRAGMENT],
        )
    )

def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "emby":
        return _run_emby(args)
    if args.command == "tailscale":
        return _run_tailscale(args)
    if args.command == "feeds":
        return _run_feeds(args)
    if args.command == "all":
        return _run_all(args)
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        log("cli", f"command failed with exit={error.returncode}", level="error")
        raise SystemExit(error.returncode)
    except (GithubError, HttpError, OSError, RuntimeError, ValueError) as error:
        log("cli", str(error), level="error")
        raise SystemExit(1)
