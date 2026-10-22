# Sækkevogn

An automated bare-metal installer for **Proxmox Backup Server**: PXE/iPXE
boot a custom-built initrd, feed it a Proxmox-compatible answer TOML over
HTTP, and it debootstraps Debian Trixie onto a ZFS mirror, installs PBS,
and reboots into a working backup server — no ISO, no Debian Installer
`partman`, no manual intervention.

*Sækkevogn* (Danish, "sack truck") is the companion project to
[Klargøring](https://github.com/knightmare2600/klargoring), which does the
same job for Proxmox VE. This repo started as a clone of Klargøring
(its full history is kept — everything up to and including
`69df4ac` is Klargøring's) with small, PBS-specific changes on top.
Internal names (`saekkevogn-installer.service`, `/opt/saekkevogn/`, the
`saekkevogn-<hex>` hostname prefix, the ISO filename) use the ASCII
spelling, the same way Klargøring uses `klargoring`.

## Status

**Untested on real hardware.** Klargøring itself is confirmed end-to-end
on real disks (2026-07-25); none of that carries over automatically.
What has been verified for Sækkevogn so far:

- The PBS repo URI/suite/component and package names, against
  pbs.proxmox.com/docs/installation.html and the live repo's own
  `Release`/`Packages` indexes (2026-10-22).
- The Proxmox archive keyring's SHA-256, re-checked against the live file
  (2026-10-22) — unchanged from Klargøring's pinned value.
- `osutil/chroot.py`'s `write_network()` against the four
  `/etc/network/interfaces` shapes it can meet (PVE-style `inet manual`,
  lo-only, missing file, already configured), in a local test harness.
- Python compiles; shell scripts pass `bash -n`; the workflow parses as YAML.

Not yet verified: a real initrd build, a real install, and the first boot
of the installed system.

## What differs from Klargøring

| Area | Klargøring (PVE) | Sækkevogn (PBS) | Why |
|---|---|---|---|
| apt repo | `debian/pve`, `pve-no-subscription` | `debian/pbs`, `pbs-no-subscription` | PBS docs |
| Main package | `proxmox-ve` | `proxmox-backup` | The full metapackage: Depends on `proxmox-default-kernel` and `proxmox-kernel-helper` (`proxmox-boot-tool`). `proxmox-backup-server` alone keeps the Debian kernel and pulls in neither. |
| `ifupdown2` | pulled in by `proxmox-ve` (hard Depends) | installed explicitly | `proxmox-backup-server` only Recommends it |
| `open-iscsi` | installed | dropped | PVE storage-backend dependency, not needed for PBS |
| Network config | flips PVE's generated `inet manual` stanza to `dhcp` | same, plus a fallback that writes `auto <nic>` / `inet dhcp` | See below |
| Architectures | amd64 + arm64 | amd64 only | See below |

### The network fallback

On PVE, `/etc/network/interfaces` gets an `iface <nic> inet manual`
stanza from `ifupdown2`'s postinst `proxmox_compatibility()`, which
Klargøring then flips to `dhcp`. That function starts with
`exit if !$haspve_common`. It only does anything when `PVE::INotify`
(`libpve-common-perl`, PVE-only) is installed, and that was confirmed by reading
`ifupdown2_3.3.0-1+pmx12`'s own postinst. On PBS it exits immediately, so
without a fallback the installed server would boot with only `lo`
configured. `write_network()` now adds a DHCP stanza for the NIC the
installer itself got its lease on when no non-`lo` interface is
configured. It relies on the target seeing the same NIC name as the
installer, because both run the same udev predictable-naming scheme on the same
hardware. That's unverified on real PBS hardware.

## Architectures

**amd64 only.** The PBS trixie repo's `Release` file lists
`Architectures: amd64`, and `binary-arm64/` returns 404 (checked
2026-10-22), unlike PVE, which is why Klargøring builds arm64 and this
doesn't. The `ARCH` env var and `<output-dir>/<ARCH>/` layout are kept
from Klargøring, but both build scripts reject anything other than
`amd64`, and the CI matrix has a single entry. If Proxmox publishes
arm64 PBS, re-enabling it is a small change.

## How it works

1. iPXE boots a custom kernel + initrd (built by
   `build/build-installer-initrd.sh`, systemd as PID 1).
2. The initrd brings up networking via DHCP, fetches the answer TOML from
   `toml_url=` on the kernel cmdline, and parses it directly with
   Python's `tomllib` (no intermediate schema).
3. It partitions the target disks, creates a ZFS mirror, and debootstraps
   Debian Trixie onto it.
4. Inside a chroot on the target: hostname/hosts/timezone/locale/keyboard,
   root password hash + SSH key injection, the PBS repo + packages,
   network config, `proxmox-boot-tool`-managed bootloader setup, and a
   `first-boot.service` that fetches and runs a site-specific script on
   the real first boot.
5. The ZFS pool is exported cleanly and the machine reboots into PBS
   (web UI on port 8007).

## Repository layout

```
installer/          the Stage 2 installer, runs inside the booted initrd
  main.py              orchestrator: load TOML -> ... -> reboot
  config.py            TOML load + validation
  logger.py            logs to /var/log/saekkevogn/install.log + console
  storage/
    detect.py            disk detection/validation (size floor, not mounted)
    partition.py         sgdisk GPT layout: bios_boot + ESP + zfs, per disk
    zfs.py               zpool/zfs dataset creation
  osutil/              (not os/ -- would shadow the stdlib os module)
    debootstrap.py       debootstrap onto /target
    chroot.py            bind-mount/chroot helper + target config
  proxmox/
    repository.py        PBS apt repo + signing key (deb822, checksummed)
    packages.py          proxmox-backup install, kernel swap, os-prober removal
  boot/
    grub.py              hostid, initramfs, proxmox-boot-tool per disk
  firstboot/
    systemd.py           fetches first-boot.sh, enables first-boot.service

build/
  build-installer-initrd.sh   Stage 1: builds the initrd + matching kernel
  build-installer-iso.sh      wraps that build's output into a bootable ISO
  grub.cfg                    the ISO's GRUB menu

.github/workflows/
  build.yml            manual workflow_dispatch: initrd+kernel, ISO, then a
                        GitHub Release

examples/
  answer.toml          generic template -- real per-site files are never
                        checked in here
```

## Building the initrd

```
sudo bash build/build-installer-initrd.sh /path/to/output-dir
```

Needs root. Produces, under `<output-dir>/amd64/`:

- `installer-initrd.img`: the packed, xz-compressed initrd
- `vmlinuz`: the matching kernel (module versions are pinned to this
  exact build; don't mix and match with another build's initrd)
- `KERNEL_VERSION`: the kernel version string

The initrd is the same as Klargøring's apart from naming. It is a plain
Debian install environment, and nothing PBS-specific goes in until Stage 2
runs on the target.

## Building the ISO

For sites where PXE isn't available. Run after the initrd build:

```
bash build/build-installer-iso.sh /path/to/initrd-output-dir /path/to/iso-output-dir
```

No root needed. Needs `grub-pc-bin`, `grub-efi-amd64-bin`, `xorriso` and
`mtools`, and produces a hybrid BIOS+UEFI
`<output-dir>/amd64/saekkevogn-installer.iso`.

Both steps also run as a manually triggered GitHub Actions workflow
(`.github/workflows/build.yml`, `workflow_dispatch` only), publishing
`vmlinuz-amd64`, `installer-initrd-amd64.img`, `KERNEL_VERSION-amd64` and
`saekkevogn-installer-amd64.iso` as a GitHub Release tagged
`saekkevogn-<date>-<suite>-<run-number>` (or whatever tag you pass in).

## Booting via iPXE

**Proposed, not yet in the live `menu.ipxe`.** This mirrors Klargøring's
stanza. The `-pbs-answer.toml` filename is a presumption pending
confirmation, because `${site-prefix}-answer.toml` at that path is already
Klargøring's PVE answer file:

```
:saekkevogn
kernel ${boot-url}/proxmox/saekkevogn/amd64/vmlinuz toml_url=${boot-url}/proxmox/${site-prefix}-pbs-answer.toml confirm-wipe no-reboot
initrd ${boot-url}/proxmox/saekkevogn/amd64/installer-initrd.img
boot
```

## Booting via ISO

As with Klargøring, the GRUB menu (`build/grub.cfg`) ships four entries,
{local tty1, serial ttyS0} × {auto-reboot, no-reboot}, all with a
placeholder `toml_url=http://CHANGE-ME/answer.toml`. Press `e`, replace
`CHANGE-ME`, then boot with `Ctrl-X`/`F10`. An unedited boot fails to
fetch the TOML and exits without touching any disk.

### Kernel cmdline options

- `toml_url=<url>`: **required**. Fetched with `curl -fsSL`, over plain
  HTTP in every example here. Bear that in mind if the file carries
  secrets (password hash, SSH keys) over an untrusted network.
- `confirm-wipe`: interactive/manual runs. An operator must confirm at
  the console before any disk is touched.
- `unattended`: production PXE use. Skips the confirmation.
- `no-reboot`: stay up after a successful install, for inspection.

**The installer refuses to touch any disk unless `confirm-wipe` or
`unattended` is present.** If neither is present, it logs the disks it would wipe and
exits.

## Debugging a booted instance

- Console autologin as root on `tty1` and `ttyS0`.
- SSH: `ssh root@<hostname-or-ip>`, password `saekkevogn`. This applies only to
  the transient installer environment, never the installed target.
  Host keys are generated fresh per boot.
- Hostname is randomised per boot (`saekkevogn-<8 hex chars>`).
- `journalctl -u saekkevogn-installer --no-pager` for the installer's
  output, and `cat /run/answer.toml` for the fetched answer file.
- Wrong console keymap: `ckbcomp -layout gb | loadkeys -`.
- After a `no-reboot` run the pool is exported; inspect with
  `zpool import -R /target rpool`, and `zpool export rpool` again before
  rebooting.

## The answer TOML

PBS's own automated installer uses the same answer-file format as PVE.
pve.proxmox.com/wiki/Automated_Installation covers PBS 3.2 onwards. It
lists no PBS-specific keys, and it uses kebab-case keys, because snake_case
is deprecated from PBS 4.0. Sækkevogn uses that format, as a subset, with the
same contract as Klargøring, parsed field for field. See
`examples/answer.toml`. Currently supported:

- `disk-setup.filesystem = "zfs"` with `zfs.raid = "raid1"` across
  exactly 2 disks
- `network.source = "from-dhcp"`
- `first-boot.source = "from-url"`

## Known limitations

- Untested on real hardware (see Status).
- No PBS datastore is created, and that is deliberate. The installed server comes up
  with only the ZFS root pool, and datastores are left for whoever runs
  the server to configure (the upstream answer format has no datastore
  keys either).
- Inherited from Klargøring: single disks, RAIDZ and static networking
  aren't implemented; no QEMU integration test harness.
