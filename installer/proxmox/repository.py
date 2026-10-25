"""Adds the Proxmox apt repository the target installs from, verified
against pdm.proxmox.com/docs/installation.html (2026-10-25): deb822
.sources format, not a classic one-line deb entry, and the signing key
comes from enterprise.proxmox.com with a published checksum. The keyring
is the same Trixie-generation Proxmox archive keyring Klargøring (PVE) and
Sækkevogn (PBS) use -- the PDM docs publish the identical SHA-256.

amd64: the PDM repo (debian/pdm, pdm-no-subscription). Only the URI path
and component differ from Sækkevogn.

arm64: the PVE repo (debian/pve, pve-no-subscription) instead. The PDM
repo's own Release file lists "Architectures: amd64" only and its
binary-arm64/ index returns 404 (checked 2026-10-25) -- Proxmox publishes
no arm64 PDM at all -- but the PVE repo lists "amd64 arm64", and checked
against its binary-arm64 Packages index (2026-10-25) it carries everything
PDM needs except the PDM packages themselves, pdm-i18n and
proxmox-geojson-data: the Proxmox kernel, proxmox-kernel-helper
(proxmox-boot-tool), ZFS, ifupdown2, proxmox-termproxy,
proxmox-mini-journalreader, pve-xtermjs, novnc-pve,
libproxmox-acme-plugins and the rest. proxmox/packages.py supplies the
missing ones. A PDM host tracking the PVE repo for those shared packages
is this project's own arrangement, not something Proxmox documents.
"""
import hashlib
import subprocess

KEY_URL = "https://enterprise.proxmox.com/debian/proxmox-archive-keyring-trixie.gpg"
KEY_SHA256 = "136673be77aba35dcce385b28737689ad64fd785a797e57897589aed08db6e45"
KEY_PATH = "/usr/share/keyrings/proxmox-archive-keyring.gpg"

REPOS = {
    "amd64": ("/etc/apt/sources.list.d/pdm-install-repo.sources",
              "http://download.proxmox.com/debian/pdm", "pdm-no-subscription"),
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
