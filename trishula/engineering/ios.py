"""iOS / Xcode testing across platforms — with honest platform limits.

The hard truth first: **iOS simulator and XCUITest UI tests require macOS +
Xcode.** Apple's simulator, `xcodebuild`, and the iOS SDK ship only for
macOS; no open toolchain runs them on Windows/Linux. Trishula therefore
splits iOS testing into what CAN run where, and automates the path for what
cannot:

┌──────────────────────────────┬─────────────┬───────────────────────────────┐
│ Test kind                    │ Runs on     │ How                           │
├──────────────────────────────┼─────────────┼───────────────────────────────┤
│ SwiftPM logic tests          │ mac/win/linux│ ``swift test``               │
│ React Native unit tests      │ anywhere     │ ``npm test`` / jest          │
│ Flutter logic/widget tests   │ anywhere     │ ``flutter test``             │
│ Kotlin Multiplatform shared  │ anywhere     │ ``gradle test``              │
│ XCTest on iOS simulator      │ **macOS only**| ``xcodebuild test``         │
│ XCUITest UI tests            │ **macOS only**| ``xcodebuild test``         │
│ On-device / cloud devices    │ managed      │ Appetize / BrowserStack / …  │
└──────────────────────────────┴─────────────┴───────────────────────────────┘

For the macOS-only pieces, Trishula generates, from Windows/Linux:

* a complete **GitHub Actions ``macos-latest`` workflow** that builds and
  tests on Apple hardware in the cloud;
* exact **remote-Mac commands** (own Mac, MacStadium/MacinCloud, AWS EC2 Mac)
  over SSH;
* the artifact/IPA upload path for device-cloud services.
"""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from trishula.tools.workspace import Workspace


@dataclass
class IOSProject:
    kind: str                          # "xcode" | "swiftpm" | "react-native" | "flutter" | "unknown"
    root: str
    xcodeproj: str = ""
    xcworkspace: str = ""
    package_manifest: str = ""
    xctestplan: List[str] = field(default_factory=list)
    schemes: List[str] = field(default_factory=list)
    has_ios_dir: bool = False
    notes: str = ""


@dataclass
class TestOption:
    label: str
    runs_on: str
    available_here: bool
    commands: List[str]
    requires: str = ""


@dataclass
class IOSTestPlan:
    project: Optional[IOSProject]
    host_platform: str
    can_run_simulator_tests_locally: bool
    local: List[TestOption]
    simulator_path: str                   # "local" | "ci" | "remote-mac" | "unsupported"
    ci_workflow_yaml: str
    remote_mac_commands: List[str]
    cloud_notes: List[str]
    limitations: List[str]

    def to_dict(self) -> dict:
        return {
            "kind": self.project.kind if self.project else "unknown",
            "host_platform": self.host_platform,
            "can_run_simulator_tests_locally": self.can_run_simulator_tests_locally,
            "local": [
                {"label": o.label, "runs_on": o.runs_on, "available_here": o.available_here,
                 "commands": o.commands, "requires": o.requires}
                for o in self.local
            ],
            "simulator_path": self.simulator_path,
            "remote_mac_commands": self.remote_mac_commands,
            "cloud_notes": self.cloud_notes,
            "limitations": self.limitations,
        }


# ── detection ───────────────────────────────────────────────────────────────

def detect_ios_project(workspace: Workspace) -> Optional[IOSProject]:
    files = workspace.walk_files(max_files=2000)
    names = {f.name: f for f in files}
    rels = [workspace.rel(f) for f in files]

    def _bundle(r: str, ext: str) -> str:
        # File walks only return files; a path inside Foo<ext>/ means the
        # project bundle is the Foo<ext> directory itself.
        idx = r.find(f"{ext}/")
        if idx >= 0:
            return r[: idx + len(ext)]
        return r if r.endswith(ext) else ""

    xcodeproj = next(
        (b for b in (_bundle(r, ".xcodeproj") for r in rels) if b), ""
    )
    xcworkspace = next(
        (b for b in (_bundle(r, ".xcworkspace") for r in rels) if b), ""
    )
    package = names.get("Package.swift")
    testplans = [r for r in rels if r.endswith(".xctestplan")]
    has_ios_dir = any(r.split("/")[0] == "ios" for r in rels) or (workspace.root / "ios").is_dir()
    podfile = "Podfile" in names
    pubspec = "pubspec.yaml" in names
    pkg_json = names.get("package.json")

    kind = "unknown"
    if xcodeproj or xcworkspace:
        kind = "xcode"
    elif package is not None and not has_ios_dir:
        kind = "swiftpm"
    elif pubspec:
        kind = "flutter"
    elif pkg_json is not None and has_ios_dir:
        kind = "react-native"
    elif package is not None:
        kind = "swiftpm"
    else:
        return None

    proj = IOSProject(
        kind=kind, root=".", xcodeproj=xcodeproj, xcworkspace=xcworkspace,
        package_manifest=workspace.rel(package) if package else "",
        xctestplan=testplans, has_ios_dir=has_ios_dir,
        notes="CocoaPods present (use .xcworkspace)" if podfile else "",
    )
    # Scheme discovery needs xcodebuild (macOS); elsewhere leave empty.
    if _is_macos() and (xcodeproj or xcworkspace):
        proj.schemes = _list_schemes(workspace)
    return proj


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _list_schemes(workspace: Workspace) -> List[str]:
    import subprocess
    target = workspace.root
    try:
        out = subprocess.run(
            ["xcodebuild", "-list", "-json"], cwd=str(target),
            capture_output=True, text=True, timeout=30,
        )
        import json
        data = json.loads(out.stdout or "{}")
        return data.get("project", {}).get("schemes", []) or data.get("workspace", {}).get("schemes", [])
    except Exception:  # noqa: BLE001
        return []


# ── commands ────────────────────────────────────────────────────────────────

def xcodebuild_test_command(
    project: IOSProject,
    *,
    scheme: str = "",
    destination: str = "platform=iOS Simulator,name=iPhone 15,OS=latest",
    result_bundle: str = "TestResults",
) -> str:
    """The canonical macOS XCTest command for CI / a remote/local Mac."""
    scheme = scheme or (project.schemes[0] if project.schemes else "App")
    if project.xcworkspace:
        target = f'-workspace "{project.xcworkspace}"'
    elif project.xcodeproj:
        target = f'-project "{project.xcodeproj}"'
    else:
        target = "-scheme App"
    return (
        f"xcodebuild test {target} -scheme '{scheme}' "
        f"-destination '{destination}' "
        f"-resultBundlePath {result_bundle} -enableCodeCoverage YES | xcpretty"
    )


def _ci_workflow(project: Optional[IOSProject], scheme: str) -> str:
    scheme = scheme or (project.schemes[0] if project and project.schemes else "App")
    work_or_proj = ""
    if project and project.xcworkspace:
        work_or_proj = f'-workspace "{project.xcworkspace}"'
    elif project and project.xcodeproj:
        work_or_proj = f'-project "{project.xcodeproj}"'
    return f"""name: iOS CI (Xcode)
# Generated by Trishula — runs macOS-only XCTest/XCUITest on Apple hardware in
# the cloud, so Windows/Linux contributors get iOS CI without a Mac.
on:
  push: {{ branches: [ main ] }}
  pull_request:
  workflow_dispatch:

jobs:
  ios-test:
    name: Build & test (iOS Simulator)
    runs-on: macos-14
    timeout-minutes: 45
    steps:
      - uses: actions/checkout@v4
      - name: Select Xcode
        run: sudo xcode-select -s /Applications/Xcode_15.4.app/Contents/Developer || true
      - name: Show environment
        run: |
          xcodebuild -version
          xcrun simctl list devices available | head -40
      - name: Install CocoaPods dependencies
        run: |
          if [ -f Podfile ]; then
            pod install
          fi
      - name: Pre-boot simulator
        run: xcrun simctl boot "iPhone 15" || true
      - name: Run tests
        run: |
          set -o pipefail
          xcodebuild test {work_or_proj} -scheme '{scheme}' \\
            -destination 'platform=iOS Simulator,name=iPhone 15,OS=latest' \\
            -resultBundlePath TestResults -enableCodeCoverage YES | xcpretty
      - name: Upload results
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: xcode-test-results
          path: TestResults*
"""


def _remote_mac_commands(project: Optional[IOSProject], scheme: str) -> List[str]:
    xc = xcodebuild_test_command(project, scheme=scheme) if project else "xcodebuild test ..."
    return [
        "# Option A — your own/a rented Mac over SSH (MacStadium, MacinCloud, AWS EC2 Mac):",
        "ssh user@your-mac 'cd ~/repo && git pull && " + xc.replace("'", "'\\''") + "'",
        "# Option B — one-off: copy the source and run remotely:",
        "rsync -az --exclude .git ./ user@your-mac:~/ios-build/",
        f"ssh user@your-mac 'cd ~/ios-build && {xc}'",
    ]


# ── plan ────────────────────────────────────────────────────────────────────

def ios_test_plan(
    workspace: Workspace,
    *,
    goal: str = "",
    scheme: str = "",
) -> IOSTestPlan:
    project = detect_ios_project(workspace)
    host = platform.system()  # "Darwin" | "Windows" | "Linux"
    is_mac = _is_macos()

    local: List[TestOption] = []
    limitations: List[str] = []

    # SwiftPM logic tests — cross-platform
    have_swift = _which("swift")
    if project and (project.kind == "swiftpm" or project.package_manifest):
        local.append(TestOption(
            "SwiftPM logic tests (Foundation-only, cross-platform)",
            "mac/windows/linux",
            available_here=bool(have_swift),
            commands=["swift test"],
            requires="Swift toolchain (swift.org) — note: UIKit/AppKit imports are Apple-only",
        ))

    # React Native
    have_npm = _which("npm")
    if project and project.kind == "react-native":
        local.append(TestOption(
            "React Native JS unit tests (jest)",
            "anywhere",
            available_here=bool(have_npm),
            commands=["npm ci", "npm test -- --watchAll=false"],
            requires="Node.js",
        ))

    # Flutter
    have_flutter = _which("flutter")
    if project and project.kind == "flutter":
        local.append(TestOption(
            "Flutter unit/widget tests",
            "anywhere",
            available_here=bool(have_flutter),
            commands=["flutter pub get", "flutter test"],
            requires="Flutter SDK (integration tests still need a Mac for iOS)",
        ))

    # Xcode XCTest / XCUITest
    if project and project.kind == "xcode":
        if is_mac:
            local.append(TestOption(
                "XCTest / XCUITest on iOS Simulator",
                "macOS only",
                available_here=bool(_which("xcodebuild")),
                commands=[xcodebuild_test_command(project, scheme=scheme)],
                requires="Xcode + xcodebuild",
            ))
        else:
            limitations.append(
                "iOS Simulator and XCUITest cannot run on "
                f"{host}: Apple ships the simulator/SDK for macOS only. "
                "Use the generated macOS CI workflow or a remote Mac."
            )

    simulator_path = "local" if is_mac and project and project.kind == "xcode" else (
        "ci" if project and project.kind in {"xcode", "flutter", "react-native"} else "unsupported"
    )

    cloud_notes = [
        "Appetize.io / BrowserStack App Live: upload a signed .app/.ipa build "
        "(built on macOS CI) to run on hosted iOS devices without owning a Mac.",
        "Codemagic / Bitrise / GitHub Actions macos runners: managed macOS build+test.",
        "AWS EC2 Mac instances (mac1.metal) for dedicated cloud macOS.",
    ]

    if not project:
        limitations.append(
            "No iOS project markers found (.xcodeproj/.xcworkspace/Package.swift/"
            "ios/ dir). Point at the project root containing Xcode/SwiftPM files."
        )

    return IOSTestPlan(
        project=project,
        host_platform=host,
        can_run_simulator_tests_locally=is_mac and bool(_which("xcodebuild")),
        local=local,
        simulator_path=simulator_path,
        ci_workflow_yaml=_ci_workflow(project, scheme),
        remote_mac_commands=_remote_mac_commands(project, scheme),
        cloud_notes=cloud_notes,
        limitations=limitations,
    )


def _which(cmd: str) -> str:
    import shutil
    return shutil.which(cmd) or ""
