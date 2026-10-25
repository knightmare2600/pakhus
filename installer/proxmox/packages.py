"""Proxmox Datacenter Manager package installation, adapted from
Sækkevogn's PBS sequence (itself from Klargøring's PVE one) and
pdm.proxmox.com/docs/installation.html for a chroot build rather than a
live system.

PDM docs offer two metapackages: proxmox-datacenter-manager-container-meta
("keep the current (Debian) kernel and install a minimal set of required
packages") and proxmox-datacenter-manager-meta ("the Proxmox default
kernel with ZFS support"). This project boots a ZFS root via
proxmox-boot-tool, so it needs proxmox-datacenter-manager-meta: checked
against the repo's own Packages index (2026-10-25), it Depends on
proxmox-default-kernel and proxmox-kernel-helper (which ships
proxmox-boot-tool); the container-meta pulls in neither.

arm64 (see install_arm64() below): Proxmox publishes no arm64 PDM. The
server, client and UI packages come from qemus/proxmox-datacenter-arm64 --
an unofficial, community build (its own README: "not affiliated,
sponsored, or endorsed by Proxmox Server Solutions GmbH"), each pinned
here by SHA-256. Everything else is official.

Here there's no live system to reboot mid-install -- the kernel is only
ever finalised once, in boot/grub.py's initramfs/grub regeneration at the
very end, after every package change below has already settled.
"""
import hashlib
import os
import shutil
import subprocess

from proxmox import repository

# Unlike qemus/proxmox-backup-arm64 (see Sækkevogn), this repo's GitHub
# releases do carry a per-asset digest. Each pin below was computed from
# the asset downloaded 2026-10-25 and matched GitHub's own sha256 digest
# for it. That proves the bytes are the ones qemus published, not that
# they match Proxmox's source. Bumping PDM on arm64 means changing
# QEMUS_TAG, QEMUS_VERSION and every hash here.
#
# proxmox-datacenter-manager-ui is Architecture: amd64 in Proxmox's own
# repo; qemus republishes it as Architecture: all. Checked 2026-10-25: its
# payload and md5sums are identical to the official 1.1.4 .deb, and the
# control file differs only in that one Architecture line. The UI is
# versioned separately from the server -- 1.1.4 is the newest UI in the
# official repo too.
QEMUS_TAG = "1.1.9"
QEMUS_VERSION = "1.1.9"
QEMUS_URL = f"https://github.com/qemus/proxmox-datacenter-arm64/releases/download/{QEMUS_TAG}"
QEMUS_DEBS = {
    f"proxmox-datacenter-manager_{QEMUS_VERSION}_arm64.deb":
        "a8746d708865cc6c4485e2e73942fa7735e14b147345f91a236219e8631e4167",
    f"proxmox-datacenter-manager-client_{QEMUS_VERSION}_arm64.deb":
        "150b52d84d7ae995522a4ec4bb73023313d1951b49d13cc9e7a6d64cdf6db49d",
    "proxmox-datacenter-manager-ui_1.1.4_all.deb":
        "9b4f950bcfd0796c51b01d26fca84d227eb6be24e87514770d0c18042fb4efea",
}

# Inside the target. Local .debs are staged here, and the one-off apt
# state for fetching the PDM repo's Architecture: all packages lives under
# PDM_ALL_STATE, separate from the target's real apt state.
DEB_STAGING = "/var/cache/pakhus/debs"
PDM_ALL_STATE = "/var/lib/pakhus/pdm-all"
PDM_ALL_LIST = (f"deb [arch=amd64 signed-by={repository.KEY_PATH}] "
                "http://download.proxmox.com/debian/pdm trixie pdm-no-subscription\n")

# Every non-kernel package both arches install, beyond PDM itself.
# ifupdown2 is explicit for the same reason as in Sækkevogn:
# proxmox-datacenter-manager only Recommends it (checked against the PDM
# repo's Packages index, 2026-10-25), and osutil/chroot.py's
# write_network() edits ifupdown2's interfaces file with no other network
# stack on the target at all.
COMMON_PACKAGES = ["ifupdown2", "postfix", "chrony", "vim-tiny", "nano"]


def install(chroot, arch, log):
    log.info("apt-get update && full-upgrade (target chroot)")
    chroot.run(["apt-get", "update"])
    chroot.run(["apt-get", "-y", "full-upgrade"])

    log.info("installing proxmox-default-kernel")
    chroot.run(["apt-get", "-y", "install", "proxmox-default-kernel"])

    if arch == "arm64":
        install_arm64(chroot, log)
    else:
        log.info(f"installing proxmox-datacenter-manager-meta + {' + '.join(COMMON_PACKAGES)}")
        chroot.run(["apt-get", "-y", "install", "proxmox-datacenter-manager-meta", *COMMON_PACKAGES])

    log.info("removing the generic Debian kernel (proxmox-default-kernel replaces it)")
    result = chroot.run(["dpkg-query", "-W", "-f=${Package}\\n"], capture_output=True, text=True)
    debian_kernels = [p for p in result.stdout.splitlines()
                       if p.startswith("linux-image-") and "pve" not in p]
    if debian_kernels:
        chroot.run(["apt-get", "-y", "remove", "--purge", *debian_kernels])

    log.info("removing os-prober (per upstream install notes)")
    chroot.run(["apt-get", "-y", "remove", "--purge", "os-prober"], check=False)

    chroot.run(["apt-get", "-y", "autoremove", "--purge"])


def _fetch_qemus_debs(chroot, log):
    staged = []
    for deb, expected in QEMUS_DEBS.items():
        url = f"{QEMUS_URL}/{deb}"
        path = f"{chroot.target}{DEB_STAGING}/{deb}"
        log.info(f"fetching unofficial arm64 PDM package: {url}")
        subprocess.run(["curl", "-fsSL", url, "-o", path], check=True)
        with open(path, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        if digest != expected:
            raise RuntimeError(
                f"{deb} checksum mismatch: got {digest}, expected {expected} "
                "-- refusing to install an unverified package"
            )
        log.info(f"{deb} checksum verified against the pinned value")
        staged.append(f"{DEB_STAGING}/{deb}")
    return staged


def _fetch_pdm_arch_all(chroot, log):
    """pdm-i18n, proxmox-datacenter-manager-docs and proxmox-geojson-data
    are Architecture: all, but only published in the amd64-only PDM repo
    (none of the three is in the PVE arm64 index, checked 2026-10-25) --
    an arm64 apt never reads that repo's binary-amd64 index, so it can't
    see them. Fetch them with a throwaway apt configuration pointed only at
    that index: apt itself verifies the signed InRelease against the
    Proxmox keyring and the .deb hashes against the index, exactly as for
    any normal install. Same mechanism as Sækkevogn's PBS fetch. Nothing
    here touches the target's real apt sources, lists or dpkg state.

    proxmox-datacenter-manager-docs is pinned to the server's own version
    (the server Depends on it, unversioned) so the docs match the server;
    the other two have no versioned dependency, newest wins.
    """
    state = f"{chroot.target}{PDM_ALL_STATE}"
    os.makedirs(f"{state}/lists/partial", exist_ok=True)
    os.makedirs(f"{state}/cache/archives/partial", exist_ok=True)
    with open(f"{state}/pdm.list", "w") as f:
        f.write(PDM_ALL_LIST)

    apt_opts = [
        "-o", f"Dir::Etc::SourceList={PDM_ALL_STATE}/pdm.list",
        "-o", "Dir::Etc::SourceParts=/dev/null",
        "-o", f"Dir::State::Lists={PDM_ALL_STATE}/lists",
        "-o", f"Dir::Cache={PDM_ALL_STATE}/cache",
        "-o", "Dir::State::status=/dev/null",
        "-o", "APT::Architecture=amd64",
        "-o", "APT::Architectures::=amd64",
    ]
    wanted = ["pdm-i18n", f"proxmox-datacenter-manager-docs={QEMUS_VERSION}", "proxmox-geojson-data"]
    log.info(f"fetching {wanted} (Architecture: all) from the PDM repo's amd64 index")
    chroot.run(["apt-get", *apt_opts, "update"])
    # apt-get download writes into the current directory, and chroot(1)
    # always starts the command in /.
    chroot.run(["sh", "-c", 'cd "$0" && exec apt-get "$@"', DEB_STAGING,
                *apt_opts, "download", *wanted])
    shutil.rmtree(state)

    names = ("pdm-i18n_", f"proxmox-datacenter-manager-docs_{QEMUS_VERSION}_", "proxmox-geojson-data_")
    debs = sorted(f"{DEB_STAGING}/{n}" for n in os.listdir(f"{chroot.target}{DEB_STAGING}")
                  if n.startswith(names) and n.endswith("_all.deb"))
    if len(debs) != 3:
        raise RuntimeError(f"expected pdm-i18n + proxmox-datacenter-manager-docs + "
                           f"proxmox-geojson-data debs, found {debs}")
    return debs


def install_arm64(chroot, log):
    """Mirrors proxmox-datacenter-manager-meta's own Depends
    (proxmox-archive-keyring, proxmox-datacenter-manager,
    proxmox-datacenter-manager-client, proxmox-datacenter-manager-docs,
    proxmox-datacenter-manager-ui, proxmox-default-kernel,
    proxmox-kernel-helper, systemd-sysv) package by package, since that
    metapackage isn't installable on arm64 either.

    libproxmox-acme-plugins is added explicitly: the official 1.1.9 server
    Depends on it, but qemus's build of the same version doesn't (checked
    2026-10-25 by diffing the two Depends fields -- qemus's build.sh strips
    it from debian/control along with build-only entries). Without it,
    ACME DNS-challenge plugins would be missing.

    Every remaining dependency of the qemus packages was checked present by
    name in Debian trixie arm64 + the PVE arm64 repo + the three
    Architecture: all packages (2026-10-25), with the versioned ones
    satisfied (proxmox-mini-journalreader 1.7, novnc-pve 1.7.0-2,
    pve-xtermjs 6.0.0-2). The server's Recommends of
    proxmox-datacenter-manager-meta has no arm64 candidate and is skipped
    by apt without error.

    The installed system has no apt source for the six non-repo packages,
    so they don't update with apt upgrade -- see README.md "arm64".
    """
    os.makedirs(f"{chroot.target}{DEB_STAGING}", exist_ok=True)
    local_debs = [*_fetch_qemus_debs(chroot, log), *_fetch_pdm_arch_all(chroot, log)]

    packages = ["proxmox-archive-keyring", "proxmox-kernel-helper", "systemd-sysv",
                "libproxmox-acme-plugins", *COMMON_PACKAGES]
    log.info(f"installing {packages} + local {[os.path.basename(d) for d in local_debs]}")
    chroot.run(["apt-get", "-y", "install", *packages, *local_debs])

    shutil.rmtree(f"{chroot.target}{DEB_STAGING}")
