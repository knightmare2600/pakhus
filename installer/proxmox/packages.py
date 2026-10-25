"""Proxmox Backup Server package installation, adapted from Klargøring's
PVE sequence (itself from pve.proxmox.com/wiki/Install_Proxmox_VE_on_Debian_13_Trixie)
and pbs.proxmox.com/docs/installation.html for a chroot build rather than
a live system.

PBS docs offer two metapackages: proxmox-backup-server ("keep the current
(Debian) kernel and install a minimal set of required packages") and
proxmox-backup ("the Proxmox kernel with ZFS support, and a set of common
and useful packages" -- i.e. what the PBS ISO gives you). This project
boots a ZFS root via proxmox-boot-tool, so it needs proxmox-backup:
checked against the repo's own Packages index (2026-10-22), it Depends on
proxmox-default-kernel and proxmox-kernel-helper (which ships
proxmox-boot-tool); proxmox-backup-server alone pulls in neither.

arm64 (see install_arm64() below): Proxmox publishes no arm64 PBS. The
server package comes from qemus/proxmox-backup-arm64 -- an unofficial,
community build (its own README: "not affiliated, sponsored, or endorsed
by Proxmox Server Solutions GmbH"), pinned here by version and SHA-256.
Everything else is official.

Here there's no live system to reboot mid-install -- the kernel is only
ever finalised once, in boot/grub.py's initramfs/grub regeneration at the
very end, after every package change below has already settled.
"""
import hashlib
import os
import shutil
import subprocess

from proxmox import repository

# qemus/proxmox-backup-arm64 publishes no checksums of its own (no
# SHA256SUMS asset; the GitHub API's per-asset digest field is null for
# every 4.2.8 asset, checked 2026-10-25), so this pin is trust-on-first-use:
# it guarantees every install gets the same bytes, not that those bytes
# match Proxmox's source. Hash computed from the release asset downloaded
# 2026-10-25. Bumping PBS on arm64 means changing all three values here.
QEMUS_TAG = "4.2.8"
QEMUS_VERSION = "4.2.8-1"
QEMUS_SHA256 = "7c83c859236fa3afd05d1fb7ed92aa78aa1e5ce9d0ba7a9241f2381c4ac9c760"
QEMUS_DEB = f"proxmox-backup-server_{QEMUS_VERSION}_arm64.deb"
QEMUS_URL = f"https://github.com/qemus/proxmox-backup-arm64/releases/download/{QEMUS_TAG}/{QEMUS_DEB}"

# Inside the target. Local .debs are staged here, and the one-off apt
# state for fetching the PBS repo's Architecture: all packages lives under
# PBS_ALL_STATE, separate from the target's real apt state.
DEB_STAGING = "/var/cache/saekkevogn/debs"
PBS_ALL_STATE = "/var/lib/saekkevogn/pbs-all"
PBS_ALL_LIST = (f"deb [arch=amd64 signed-by={repository.KEY_PATH}] "
                "http://download.proxmox.com/debian/pbs trixie pbs-no-subscription\n")

# Every non-kernel package both arches install, beyond the PBS server
# itself. ifupdown2 is explicit here, unlike Klargøring: proxmox-ve
# hard-Depends on "ifupdown2 | ifenslave", but proxmox-backup-server only
# Recommends ifupdown2 (checked against both repos' Packages indexes,
# 2026-10-22). Recommends are installed by apt's default config today, but
# osutil/chroot.py's write_network() edits ifupdown2's interfaces file and
# the target has no other network stack at all -- don't leave that resting
# on an apt default. open-iscsi is dropped: it was a PVE storage-backend
# dependency from the upstream PVE notes, not needed by a backup server.
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
        log.info(f"installing proxmox-backup + {' + '.join(COMMON_PACKAGES)}")
        chroot.run(["apt-get", "-y", "install", "proxmox-backup", *COMMON_PACKAGES])

    log.info("removing the generic Debian kernel (proxmox-default-kernel replaces it)")
    result = chroot.run(["dpkg-query", "-W", "-f=${Package}\\n"], capture_output=True, text=True)
    debian_kernels = [p for p in result.stdout.splitlines()
                       if p.startswith("linux-image-") and "pve" not in p]
    if debian_kernels:
        chroot.run(["apt-get", "-y", "remove", "--purge", *debian_kernels])

    log.info("removing os-prober (per upstream install notes)")
    chroot.run(["apt-get", "-y", "remove", "--purge", "os-prober"], check=False)

    chroot.run(["apt-get", "-y", "autoremove", "--purge"])


def _fetch_qemus_server(chroot, log):
    path = f"{chroot.target}{DEB_STAGING}/{QEMUS_DEB}"
    log.info(f"fetching unofficial arm64 PBS server package: {QEMUS_URL}")
    subprocess.run(["curl", "-fsSL", QEMUS_URL, "-o", path], check=True)
    with open(path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    if digest != QEMUS_SHA256:
        raise RuntimeError(
            f"{QEMUS_DEB} checksum mismatch: got {digest}, expected {QEMUS_SHA256} "
            "-- refusing to install an unverified package"
        )
    log.info(f"{QEMUS_DEB} checksum verified against the pinned value")
    return f"{DEB_STAGING}/{QEMUS_DEB}"


def _fetch_pbs_arch_all(chroot, log):
    """pbs-i18n and proxmox-backup-docs are Architecture: all, but only
    published in the amd64-only PBS repo -- an arm64 apt never reads that
    repo's binary-amd64 index, so it can't see them. Fetch them with a
    throwaway apt configuration pointed only at that index: apt itself
    verifies the signed InRelease against the Proxmox keyring and the .deb
    hashes against the index, exactly as for any normal install (checked
    2026-10-25: a tampered InRelease is rejected outright). Nothing here
    touches the target's real apt sources, lists or dpkg state.

    proxmox-backup-docs is pinned to the server's own version so the docs
    match the server; pbs-i18n has no versioned dependency, newest wins.
    """
    state = f"{chroot.target}{PBS_ALL_STATE}"
    os.makedirs(f"{state}/lists/partial", exist_ok=True)
    os.makedirs(f"{state}/cache/archives/partial", exist_ok=True)
    with open(f"{state}/pbs.list", "w") as f:
        f.write(PBS_ALL_LIST)

    apt_opts = [
        "-o", f"Dir::Etc::SourceList={PBS_ALL_STATE}/pbs.list",
        "-o", "Dir::Etc::SourceParts=/dev/null",
        "-o", f"Dir::State::Lists={PBS_ALL_STATE}/lists",
        "-o", f"Dir::Cache={PBS_ALL_STATE}/cache",
        "-o", "Dir::State::status=/dev/null",
        "-o", "APT::Architecture=amd64",
        "-o", "APT::Architectures::=amd64",
    ]
    wanted = ["pbs-i18n", f"proxmox-backup-docs={QEMUS_VERSION}"]
    log.info(f"fetching {wanted} (Architecture: all) from the PBS repo's amd64 index")
    chroot.run(["apt-get", *apt_opts, "update"])
    # apt-get download writes into the current directory, and chroot(1)
    # always starts the command in /.
    chroot.run(["sh", "-c", 'cd "$0" && exec apt-get "$@"', DEB_STAGING,
                *apt_opts, "download", *wanted])
    shutil.rmtree(state)

    names = ("pbs-i18n_", f"proxmox-backup-docs_{QEMUS_VERSION}_")
    debs = sorted(f"{DEB_STAGING}/{n}" for n in os.listdir(f"{chroot.target}{DEB_STAGING}")
                  if n.startswith(names) and n.endswith("_all.deb"))
    if len(debs) != 2:
        raise RuntimeError(f"expected pbs-i18n + proxmox-backup-docs debs, found {debs}")
    return debs


def install_arm64(chroot, log):
    """Mirrors proxmox-backup's own Depends (proxmox-archive-keyring,
    proxmox-backup-client, proxmox-backup-server, proxmox-default-kernel,
    proxmox-kernel-helper, systemd-sysv) package by package, since that
    metapackage doesn't exist for arm64 either. Every dependency of the
    qemus server package was checked present by name in Debian trixie
    arm64 + the PVE arm64 repo + the two Architecture: all packages
    (2026-10-25); its only absent Recommends
    (proxmox-network-interface-pinning) is skipped by apt without error.

    The installed system has no apt source for these three packages, so
    they don't update with apt upgrade -- see README.md "arm64".
    """
    os.makedirs(f"{chroot.target}{DEB_STAGING}", exist_ok=True)
    local_debs = [_fetch_qemus_server(chroot, log), *_fetch_pbs_arch_all(chroot, log)]

    packages = ["proxmox-archive-keyring", "proxmox-backup-client", "proxmox-kernel-helper",
                "systemd-sysv", *COMMON_PACKAGES]
    log.info(f"installing {packages} + local {[os.path.basename(d) for d in local_debs]}")
    chroot.run(["apt-get", "-y", "install", *packages, *local_debs])

    shutil.rmtree(f"{chroot.target}{DEB_STAGING}")
