#!/usr/bin/env python3
"""Report the exact Volta iOS TestFlight release state without collapsing gates.

This check is intentionally local and read-only. It proves that the checked-in iOS
candidate is newer than the last known Internal Testing assignment and, when supplied with
a fresh timestamp, the current highest App Store Connect build. Archive inspection, upload,
processing, tester assignment, and external Beta App Review remain separate states.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


APP_DEBUG_CONFIG = "A100000E000000000000001"
APP_RELEASE_CONFIG = "A100000F000000000000001"
EXPECTED_BUNDLE_ID = "tech.arsik.volta"
RELEASE_RELATED_PREFIXES = (
    "ios/",
    "docs/appstore/",
    "RELEASE_RUNBOOK.md",
    "qa/coverage-summary.md",
    "qa/run-log.md",
    "qa/test-cases.csv",
    "scripts/qa_testflight_candidate.py",
    "tests/test_ios_release_contract.py",
    "tests/test_testflight_candidate.py",
)
PUBLIC_LEGAL_URLS = {
    "privacy": "https://voltago.app/privacy",
    "terms": "https://voltago.app/terms",
    "support": "https://voltago.app/support",
}
EXPECTED_RELEASE_URL = "https://voltago.app"
ASC_RECEIPT_MAX_AGE = timedelta(hours=2)


@dataclass(frozen=True)
class ProjectCandidate:
    version: str
    build: int
    bundle_id: str
    team_id: str
    automatic_signing: bool


@dataclass(frozen=True)
class PublicPages:
    checked: bool
    ok: bool
    pages: dict[str, bool]
    failure: str | None = None

    @classmethod
    def unchecked(cls) -> "PublicPages":
        return cls(False, False, {name: False for name in PUBLIC_LEGAL_URLS}, "not checked")

    @classmethod
    def confirmed_for_tests(cls) -> "PublicPages":
        return cls(True, True, {name: True for name in PUBLIC_LEGAL_URLS})


@dataclass(frozen=True)
class ArchiveEvidence:
    checked: bool
    ok: bool
    signed: bool
    app_path: str | None = None
    failure: str | None = None

    @classmethod
    def unchecked(cls) -> "ArchiveEvidence":
        return cls(False, False, False, failure="not checked")

    @classmethod
    def confirmed_for_tests(cls) -> "ArchiveEvidence":
        return cls(True, True, True, app_path="/test/Volta.app")


def load_state(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("TestFlight state root must be an object")
    return payload


def _configuration(project: str, identifier: str) -> str:
    marker = f"{identifier} /*"
    start = project.find(marker)
    if start < 0:
        raise ValueError(f"missing Xcode configuration {identifier}")
    next_config = project.find("\n\t\tA1", start + len(marker))
    return project[start : next_config if next_config >= 0 else len(project)]


def _setting(block: str, name: str) -> str:
    match = re.search(rf"^\s*{re.escape(name)}\s*=\s*\"?([^;\"\n]+)\"?;", block, re.M)
    if not match:
        raise ValueError(f"missing Xcode setting {name}")
    return match.group(1).strip()


def read_project_candidate(path: Path) -> ProjectCandidate:
    project = path.read_text(encoding="utf-8")
    blocks = [
        _configuration(project, APP_DEBUG_CONFIG),
        _configuration(project, APP_RELEASE_CONFIG),
    ]
    settings = []
    for block in blocks:
        raw_build = _setting(block, "CURRENT_PROJECT_VERSION")
        if not raw_build.isdigit():
            raise ValueError("CURRENT_PROJECT_VERSION must be numeric")
        settings.append(
            ProjectCandidate(
                version=_setting(block, "MARKETING_VERSION"),
                build=int(raw_build),
                bundle_id=_setting(block, "PRODUCT_BUNDLE_IDENTIFIER"),
                team_id=_setting(block, "DEVELOPMENT_TEAM"),
                automatic_signing=_setting(block, "CODE_SIGN_STYLE") == "Automatic",
            )
        )
    if settings[0] != settings[1]:
        raise ValueError("Debug and Release app version/signing settings differ")
    return settings[0]


def _run_text(argv: Sequence[str], cwd: Path) -> str:
    result = subprocess.run(
        list(argv),
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return result.stdout


def _git_dirty_paths(root: Path) -> list[str]:
    output = _run_text(("git", "status", "--porcelain"), root)
    return sorted(
        line[3:].strip()
        for line in output.splitlines()
        if len(line) >= 4 and line[3:].strip()
    )


def _is_release_related(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix) for prefix in RELEASE_RELATED_PREFIXES)


def probe_public_pages(timeout: float = 8.0) -> PublicPages:
    pages: dict[str, bool] = {}
    body_hashes: list[str] = []
    failures: list[str] = []
    placeholder_markers = ("[owner", "[support", "[privacy", "[legal", "owner-domain")
    for name, url in PUBLIC_LEGAL_URLS.items():
        try:
            request = Request(url, headers={"User-Agent": "Volta-Go-TestFlight-QA/1.0"})
            with urlopen(request, timeout=timeout) as response:
                body = response.read(1_000_000)
                text = body.decode("utf-8", errors="replace").lower()
                effective = response.geturl()
                status = getattr(response, "status", None)
        except (OSError, URLError) as exc:
            pages[name] = False
            failures.append(f"{name}: {exc.__class__.__name__}")
            continue
        body_hashes.append(hashlib.sha256(body).hexdigest())
        page_ok = (
            status == 200
            and effective == url
            and name in text
            and not any(marker in text for marker in placeholder_markers)
        )
        pages[name] = page_ok
        if not page_ok:
            failures.append(f"{name}: not a distinct public TestFlight-beta page")
    if len(body_hashes) == len(PUBLIC_LEGAL_URLS) and len(set(body_hashes)) != len(body_hashes):
        failures.append("legal routes return duplicate payloads (likely the SPA shell)")
    ok = len(pages) == len(PUBLIC_LEGAL_URLS) and all(pages.values()) and not failures
    return PublicPages(True, ok, pages, "; ".join(failures) if failures else None)


def _safe_https_url(value: object, expected: str) -> bool:
    if not isinstance(value, str) or value != expected:
        return False
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )


def inspect_archive(path: Path, candidate: ProjectCandidate, root: Path) -> ArchiveEvidence:
    app_path = path
    if path.suffix == ".xcarchive":
        app_path = path / "Products/Applications/Volta.app"
    info_path = app_path / "Info.plist"
    if not info_path.is_file():
        return ArchiveEvidence(True, False, False, str(app_path), "Info.plist is missing")
    try:
        with info_path.open("rb") as handle:
            info = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException) as exc:
        return ArchiveEvidence(True, False, False, str(app_path), exc.__class__.__name__)

    expected_urls = {
        "VoltaReleaseBaseURL": EXPECTED_RELEASE_URL,
        "VoltaPrivacyPolicyURL": PUBLIC_LEGAL_URLS["privacy"],
        "VoltaTermsURL": PUBLIC_LEGAL_URLS["terms"],
        "VoltaSupportURL": PUBLIC_LEGAL_URLS["support"],
    }
    failures = []
    if info.get("CFBundleIdentifier") != candidate.bundle_id:
        failures.append("bundle ID mismatch")
    if info.get("CFBundleShortVersionString") != candidate.version:
        failures.append("marketing version mismatch")
    if str(info.get("CFBundleVersion")) != str(candidate.build):
        failures.append("build mismatch")
    for key, expected in expected_urls.items():
        if not _safe_https_url(info.get(key), expected):
            failures.append(f"{key} mismatch")

    signature = subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", str(app_path)],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    signed = signature.returncode == 0
    if not signed:
        failures.append("archive is not signed with verifiable code signing")
    return ArchiveEvidence(
        checked=True,
        ok=not failures,
        signed=signed,
        app_path=str(app_path),
        failure="; ".join(failures) if failures else None,
    )


def _fresh_asc_receipt(raw: str | None, now: datetime) -> tuple[bool, str | None]:
    if not raw:
        return False, "missing checked-at timestamp"
    try:
        checked_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False, "checked-at timestamp is invalid"
    if checked_at.tzinfo is None:
        return False, "checked-at timestamp must include a timezone"
    age = now - checked_at.astimezone(timezone.utc)
    if age < timedelta(minutes=-5):
        return False, "checked-at timestamp is in the future"
    if age > ASC_RECEIPT_MAX_AGE:
        return False, "checked-at timestamp is older than two hours"
    return True, None


def build_report(
    *,
    root: Path,
    highest_asc_build: int | None,
    asc_checked_at: str | None,
    identity_output: str | None = None,
    dirty_paths: list[str] | None = None,
    public_pages: PublicPages | None = None,
    archive: ArchiveEvidence | None = None,
) -> dict:
    state = load_state(root / "docs/appstore/testflight-state.json")
    project = read_project_candidate(root / "ios/Volta/Volta.xcodeproj/project.pbxproj")
    paywall = (root / "ios/Volta/Volta/PaywallConfig.swift").read_text(encoding="utf-8")
    identities = identity_output
    if identities is None:
        identities = _run_text(("security", "find-identity", "-v", "-p", "codesigning"), root)
    paths = sorted(dirty_paths if dirty_paths is not None else _git_dirty_paths(root))
    release_dirty = [path for path in paths if _is_release_related(path)]
    unrelated_dirty = [path for path in paths if path not in release_dirty]
    pages = public_pages or PublicPages.unchecked()
    archive_evidence = archive or ArchiveEvidence.unchecked()
    generated_at = datetime.now(timezone.utc)

    candidate_state = state["candidate"]
    previous = state["last_known_internal_assignment"]
    checks = {
        "bundle_id": project.bundle_id == EXPECTED_BUNDLE_ID == state.get("bundle_id"),
        "candidate_version_matches": project.version == candidate_state.get("version"),
        "candidate_build_matches": project.build == candidate_state.get("build"),
        "outranks_last_internal_assignment": project.build > int(previous.get("build", -1)),
        "paywall_off": "static let enabled = false" in paywall,
        "automatic_signing": project.automatic_signing,
        "team_configured": bool(project.team_id),
        "release_tree_frozen": not release_dirty,
    }

    blockers: list[str] = []
    if not all(checks.values()):
        blockers.append("one or more repository TestFlight candidate checks failed")
    if release_dirty:
        blockers.append("release-related uncommitted changes must be committed before archive")

    asc_fresh, asc_freshness_failure = _fresh_asc_receipt(asc_checked_at, generated_at)
    asc_reconciled = highest_asc_build is not None and asc_fresh
    outranks_asc = highest_asc_build is not None and project.build > highest_asc_build
    if highest_asc_build is None:
        blockers.append("highest App Store Connect build has not been supplied")
    elif not asc_fresh:
        blockers.append(
            "highest App Store Connect build needs a fresh checked-at receipt"
            + (f": {asc_freshness_failure}" if asc_freshness_failure else "")
        )
    elif not outranks_asc:
        blockers.append(
            f"candidate build {project.build} does not outrank App Store Connect build "
            f"{highest_asc_build}"
        )

    if not archive_evidence.ok:
        blockers.append(
            "no signed archive with verified embedded release URLs is available"
            + (f": {archive_evidence.failure}" if archive_evidence.failure else "")
        )

    if not pages.ok:
        blockers.append(
            "external friends/family testing still needs distinct public TestFlight-beta Privacy, Terms, and Support pages"
            + (f": {pages.failure}" if pages.failure else "")
        )

    repo_ready = all(checks.values()) and (highest_asc_build is None or outranks_asc)
    archive_inputs_ready = repo_ready and asc_reconciled and outranks_asc
    archive_ready = archive_inputs_ready and archive_evidence.ok
    friends_family_inputs_ready = archive_ready and pages.ok
    local_distribution = "Apple Distribution:" in identities
    if not local_distribution:
        signing_warning = (
            "No local Apple Distribution identity was found. Organizer cloud-signing "
            "availability is unknown until Xcode proves it."
        )
    else:
        signing_warning = None

    blockers.extend(
        [
            "no Organizer upload acceptance has been verified",
            "no processed build and internal tester-group assignment has been verified",
            "external friends/family access still requires an external group and Beta App Review",
        ]
    )

    return {
        "schema_version": 1,
        "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        "ok": archive_ready,
        "candidate": asdict(project),
        "last_known_internal_assignment": previous,
        "app_store_connect": {
            "reconciled": asc_reconciled,
            "highest_build": highest_asc_build,
            "checked_at": asc_checked_at,
            "fresh": asc_fresh,
            "freshness_failure": asc_freshness_failure,
            "candidate_outranks_highest": outranks_asc,
        },
        "checks": checks,
        "states": {
            "repo_ready": repo_ready,
            "archive_inputs_ready": archive_inputs_ready,
            "archive_ready": archive_ready,
            "friends_family_inputs_ready": friends_family_inputs_ready,
            "upload_accepted": False,
            "testflight_ready": False,
            "friends_family_ready": False,
        },
        "signing": {
            "team_id": project.team_id,
            "local_distribution_identity": local_distribution,
            "organizer_cloud_signing_possible": None,
            "warning": signing_warning,
        },
        "workspace": {
            "release_related_dirty_paths": release_dirty,
            "unrelated_dirty_paths": unrelated_dirty,
        },
        "public_pages": asdict(pages),
        "archive": asdict(archive_evidence),
        "blockers": blockers,
        "next_action": (
            "Read the highest visible build in App Store Connect and rerun with "
            "--highest-asc-build and --asc-checked-at. Then archive with Xcode Organizer "
            "and rerun with --archive before validate/upload, "
            "wait for processing, and assign the processed build to Internal Testing."
        ),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--highest-asc-build", type=int)
    parser.add_argument("--asc-checked-at")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--identity-output", help=argparse.SUPPRESS)
    parser.add_argument("--dirty-path", action="append", help=argparse.SUPPRESS)
    parser.add_argument(
        "--skip-public-check",
        action="store_true",
        help="skip legal-page probes (friends_family_inputs_ready will remain false)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    pages = PublicPages.unchecked() if args.skip_public_check else probe_public_pages()
    root = args.root.resolve()
    project = read_project_candidate(root / "ios/Volta/Volta.xcodeproj/project.pbxproj")
    archive = inspect_archive(args.archive, project, root) if args.archive else ArchiveEvidence.unchecked()
    report = build_report(
        root=root,
        highest_asc_build=args.highest_asc_build,
        asc_checked_at=args.asc_checked_at,
        identity_output=args.identity_output,
        dirty_paths=args.dirty_path,
        public_pages=pages,
        archive=archive,
    )
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["states"]["archive_ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
