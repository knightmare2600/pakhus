"""Adds the Proxmox apt repository the target installs from, verified
against pbs.proxmox.com/docs/installation.html (2026-10-22): deb822
.sources format, not a classic one-line deb entry, and the signing key
comes from enterprise.proxmox.com with a published checksum. The keyring
is the same Trixie-generation Proxmox archive keyring Klargøring uses for
PVE -- checksum re-verified against the live file on 2026-10-22,
unchanged.

amd64: the PBS repo (debian/pbs, pbs-no-subscription). Only the URI path
and component differ from Klargøring.

arm64: the PVE repo (debian/pve, pve-no-subscription) instead. The PBS
repo's own Release file lists "Architectures: amd64" only -- Proxmox
publishes no arm64 PBS at all -- but the PVE repo lists "amd64 arm64", and
checked against its binary-arm64 Packages index (2026-10-25) it carries
everything PBS needs except proxmox-backup-server itself, pbs-i18n and
proxmox-backup-docs: the Proxmox kernel, proxmox-kernel-helper
(proxmox-boot-tool), ZFS, ifupdown2, proxmox-widget-toolkit,
proxmox-termproxy, proxmox-mini-journalreader, proxmox-backup-client and
the rest. proxmox/packages.py supplies the missing three. A PBS host
tracking the PVE repo for those shared packages is this project's own
arrangement, not something Proxmox documents.
"""
import hashlib
import subprocess

KEY_URL = "https://enterprise.proxmox.com/debian/proxmox-archive-keyring-trixie.gpg"
KEY_SHA256 = "136673be77aba35dcce385b28737689ad64fd785a797e57897589aed08db6e45"
KEY_PATH = "/usr/share/keyrings/proxmox-archive-keyring.gpg"

REPOS = {
    "amd64": ("/etc/apt/sources.list.d/pbs-install-repo.sources",
              "http://download.proxmox.com/debian/pbs", "pbs-no-subscription"),
    "arm64": ("/etc/apt/sources.list.d/pve-install-repo.sources",
              "http://download.proxmox.com/debian/pve", "pve-no-subscription"),
}

SOURCES_TEMPLATE = """Types: deb
URIs: {uri}
Suites: trixie
Components: {component}
Signed-By: {key_path}
"""


def add_repository(target, arch, log):
    if arch not in REPOS:
        raise RuntimeError(f"no Proxmox repo known for arch {arch!r} (expected amd64 or arm64)")
    sources_path, uri, component = REPOS[arch]

    log.info(f"fetching Proxmox repo signing key from {KEY_URL}")
    subprocess.run(["curl", "-fsSL", KEY_URL, "-o", f"{target}{KEY_PATH}"], check=True)

    with open(f"{target}{KEY_PATH}", "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    if digest != KEY_SHA256:
        raise RuntimeError(
            f"proxmox-archive-keyring checksum mismatch: got {digest}, expected {KEY_SHA256} "
            "-- refusing to trust an unverified repo signing key"
        )
    log.info("repo signing key checksum verified")

    log.info(f"adding {uri} {component} for {arch}")
    with open(f"{target}{sources_path}", "w") as f:
        f.write(SOURCES_TEMPLATE.format(uri=uri, component=component, key_path=KEY_PATH))
