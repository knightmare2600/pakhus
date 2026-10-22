"""Adds the Proxmox Backup Server apt repository, verified against
pbs.proxmox.com/docs/installation.html (2026-10-22): deb822 .sources
format, not a classic one-line deb entry, and the signing key comes from
enterprise.proxmox.com with a published checksum. The keyring is the same
Trixie-generation Proxmox archive keyring Klargøring uses for PVE --
checksum re-verified against the live file on 2026-10-22, unchanged.

Only the URI path (pbs, not pve) and component (pbs-no-subscription)
differ from Klargøring. The repo's own Release file lists
"Architectures: amd64" only -- there is no arm64 PBS repo -- which is why
this project is amd64-only.
"""
import hashlib
import subprocess

KEY_URL = "https://enterprise.proxmox.com/debian/proxmox-archive-keyring-trixie.gpg"
KEY_SHA256 = "136673be77aba35dcce385b28737689ad64fd785a797e57897589aed08db6e45"
KEY_PATH = "/usr/share/keyrings/proxmox-archive-keyring.gpg"
SOURCES_PATH = "/etc/apt/sources.list.d/pbs-install-repo.sources"

SOURCES_CONTENT = f"""Types: deb
URIs: http://download.proxmox.com/debian/pbs
Suites: trixie
Components: pbs-no-subscription
Signed-By: {KEY_PATH}
"""


def add_repository(target, log):
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

    with open(f"{target}{SOURCES_PATH}", "w") as f:
        f.write(SOURCES_CONTENT)
