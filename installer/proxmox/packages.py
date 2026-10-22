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

Here there's no live system to reboot mid-install -- the kernel is only
ever finalised once, in boot/grub.py's initramfs/grub regeneration at the
very end, after every package change below has already settled.
"""


def install(chroot, log):
    log.info("apt-get update && full-upgrade (target chroot)")
    chroot.run(["apt-get", "update"])
    chroot.run(["apt-get", "-y", "full-upgrade"])

    log.info("installing proxmox-default-kernel")
    chroot.run(["apt-get", "-y", "install", "proxmox-default-kernel"])

    # ifupdown2 is explicit here, unlike Klargøring: proxmox-ve hard-Depends
    # on "ifupdown2 | ifenslave", but proxmox-backup-server only Recommends
    # ifupdown2 (checked against both repos' Packages indexes, 2026-10-22).
    # Recommends are installed by apt's default config today, but
    # osutil/chroot.py's write_network() edits ifupdown2's interfaces file
    # and the target has no other network stack at all -- don't leave that
    # resting on an apt default. open-iscsi is dropped: it was a PVE
    # storage-backend dependency from the upstream PVE notes, not needed by
    # a backup server.
    log.info("installing proxmox-backup + ifupdown2 + postfix + chrony + vim-tiny + nano")
    chroot.run(["apt-get", "-y", "install", "proxmox-backup", "ifupdown2", "postfix", "chrony",
                "vim-tiny", "nano"])

    log.info("removing the generic Debian kernel (proxmox-default-kernel replaces it)")
    result = chroot.run(["dpkg-query", "-W", "-f=${Package}\\n"], capture_output=True, text=True)
    debian_kernels = [p for p in result.stdout.splitlines()
                       if p.startswith("linux-image-") and "pve" not in p]
    if debian_kernels:
        chroot.run(["apt-get", "-y", "remove", "--purge", *debian_kernels])

    log.info("removing os-prober (per upstream install notes)")
    chroot.run(["apt-get", "-y", "remove", "--purge", "os-prober"], check=False)

    chroot.run(["apt-get", "-y", "autoremove", "--purge"])
