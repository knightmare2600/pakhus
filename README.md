# Sækkevogn

An automated bare-metal installer for **Proxmox Backup Server**, on
**amd64 and arm64**: PXE/iPXE boot a custom-built initrd, feed it a
Proxmox-compatible answer TOML over HTTP, and it debootstraps Debian Trixie
onto a ZFS mirror, installs PBS, and reboots into a working backup server
— no ISO in the loop, no Debian Installer `partman`, no manual
intervention.

*Sækkevogn* (Danish, "sack truck") is the companion project to
[Klargøring](https://github.com/knightmare2600/klargoring), which does the
same job for Proxmox VE. This repo started as a clone of Klargøring (its
full history is kept — everything up to and including `69df4ac` is
Klargøring's) with PBS-specific changes on top. Internal names
(`saekkevogn-installer.service`, `/opt/saekkevogn/`, the `saekkevogn-<hex>`
hostname prefix, the ISO filename) use the ASCII spelling, the same way
Klargøring uses `klargoring`.

## Status

| | amd64 | arm64 |
|---|---|---|
| Initrd + kernel + ISO build (CI) | ✅ `saekkevogn-20261025-trixie-2` | ✅ `saekkevogn-20261025-trixie-2` |
| Install on real hardware | ❌ not yet tested | ❌ not yet tested |
| PBS server package | official (PBS repo) | **unofficial** (qemus, see [arm64](#arm64)) |

Klargøring itself is confirmed end to end on real disks; none of that
carries over to Sækkevogn automatically. What *has* been verified for
Sækkevogn:

- **Builds:** GitHub Actions run `40181170038` (2026-10-25, 8 minutes)
  built the initrd, kernel and ISO for both arches and published release
  `saekkevogn-20261025-trixie-2`. The installer kernel is Debian's
  `6.12.107+deb13` on both.
- **Repos and packages:** the PBS repo URI/suite/component and package
  names, against pbs.proxmox.com/docs/installation.html and the live
  repo's own `Release`/`Packages` indexes (2026-10-22). The Proxmox
  archive keyring's SHA-256 re-checked against the live file — unchanged
  from Klargøring's pinned value.
- **Network fallback:** `write_network()` against the four
  `/etc/network/interfaces` shapes it can meet (PVE-style `inet manual`,
  lo-only, missing file, already configured), in a local test harness.
- **TOML validation:** `zfs.raid` is now actually read (see
  [The answer TOML](#the-answer-toml)); `raid1` is accepted and `raidz1`
  rejected.
- **arm64 packages:** every Depends of the qemus server package is
  present by name in Debian trixie arm64 + the PVE arm64 repo + the two
  Architecture: all PBS packages; the installer's own fetch functions ran
  end to end against the real sources (server `.deb` downloaded and
  pin-checked, a wrong pin refused, `pbs-i18n` + `proxmox-backup-docs`
  fetched through apt's own signature checking, temporary apt state
  cleaned up) (2026-10-25).

Not yet verified: a real install and first boot on either arch, and apt's
version-constraint resolution on a real arm64 system.

## Why this exists

Proxmox's automated-install mechanism is ISO-based: there's no supported
way to PXE-boot its installer kernel/initrd and point it at a small answer
file over HTTP the way Debian's preseed or RHEL's Kickstart work. Asked
about exactly this for PVE, gyptazy's answer was to PXE-boot a plain
Debian install and layer the Proxmox repo and packages on afterwards —
see Klargøring's README for the full quote. Klargøring formalised that as
one reusable initrd; Sækkevogn applies it to PBS, with the same TOML
contract, so PVE and PBS nodes in an estate are built the same way.

## Getting it

Ready-built files are on the
[Releases](https://github.com/knightmare2600/saekkevogn/releases) page
(latest: `saekkevogn-20261025-trixie-2`), one set per arch:

| Asset | What it is |
|---|---|
| `vmlinuz-<arch>` | installer kernel |
| `installer-initrd-<arch>.img` | installer initrd (~230MB amd64, ~207MB arm64) |
| `KERNEL_VERSION-<arch>` | the kernel's version string |
| `saekkevogn-installer-<arch>.iso` | both of the above wrapped in a bootable ISO |

The kernel and initrd are a matched pair (module versions are pinned to
one build), so always take both from the same release.

## How it works

1. iPXE boots the kernel + initrd (systemd as PID 1).
2. The initrd brings up networking via DHCP, fetches the answer TOML from
   `toml_url=` on the kernel cmdline, and parses it directly with
   Python's `tomllib` (no intermediate schema).
3. It partitions the target disks, creates a ZFS mirror, and debootstraps
   Debian Trixie onto it.
4. Inside a chroot on the target: hostname/hosts/timezone/locale/keyboard,
   root password hash + SSH key injection, the Proxmox repo + PBS
   packages (see [Architectures](#architectures) for where each comes
   from), network config, `proxmox-boot-tool`-managed bootloader setup,
   and a `first-boot.service` that fetches and runs a site-specific script
   on the real first boot.
5. The ZFS pool is exported cleanly and the machine reboots into PBS
   (web UI on port 8007).

## What differs from Klargøring

| Area | Klargøring (PVE) | Sækkevogn (PBS) | Why |
|---|---|---|---|
| apt repo | `debian/pve`, `pve-no-subscription` | amd64: `debian/pbs`, `pbs-no-subscription`. arm64: `debian/pve`, `pve-no-subscription` | PBS docs; arm64 see below |
| Main package | `proxmox-ve` | amd64: `proxmox-backup`. arm64: its Depends one by one, the server from qemus | `proxmox-backup` is the full metapackage: it Depends on `proxmox-default-kernel` and `proxmox-kernel-helper` (`proxmox-boot-tool`). `proxmox-backup-server` alone keeps the Debian kernel and pulls in neither. |
| `ifupdown2` | pulled in by `proxmox-ve` (hard Depends) | installed explicitly | `proxmox-backup-server` only Recommends it |
| `open-iscsi` | installed | dropped | PVE storage-backend dependency, not needed for PBS |
| Network config | flips PVE's generated `inet manual` stanza to `dhcp` | same, plus a fallback that writes `auto <nic>` / `inet dhcp` | See below |
| Architectures | amd64 + arm64 | amd64 + arm64 (arm64 PBS server unofficial) | See below |

### The network fallback

On PVE, `/etc/network/interfaces` gets an `iface <nic> inet manual`
stanza from `ifupdown2`'s postinst `proxmox_compatibility()`, which
Klargøring then flips to `dhcp`. That function starts with
`exit if !$haspve_common`: it only does anything when `PVE::INotify`
(`libpve-common-perl`, PVE-only) is installed — confirmed by reading
`ifupdown2_3.3.0-1+pmx12`'s own postinst. On PBS it exits immediately, so
without a fallback the installed server would boot with only `lo`
configured. `write_network()` adds a DHCP stanza for the NIC the
installer itself got its lease on, whenever no non-`lo` interface is
configured. That relies on the target seeing the same NIC name as the
installer (same hardware, same udev predictable-naming scheme) —
unverified on real PBS hardware.

## Architectures

amd64 and arm64, with the same build setup as Klargøring
(`ARCH=amd64|arm64`, `<output-dir>/<ARCH>/`, native `ubuntu-24.04-arm` CI
runner). The initrd is plain Debian on both; everything PBS-specific
happens at install time, on the target.

### arm64

**Proxmox publishes no arm64 PBS.** The PBS trixie repo's `Release` file
lists `Architectures: amd64`, and `binary-arm64/` returns 404 (checked
2026-10-22). Asked directly, gyptazy replied: "Not that I'm aware of. But
there're third-party scripts from the community to archive this", and
pointed at
[qemus/proxmox-backup-arm64](https://github.com/qemus/proxmox-backup-arm64),
whose own README says it is "not affiliated, sponsored, or endorsed by
Proxmox Server Solutions GmbH". qemus publishes `.deb` files only, with
no installer, so Sækkevogn wires them into the install. On arm64,
`proxmox/packages.py` installs from three sources:

| Package(s) | Source | Official? |
|---|---|---|
| Kernel, `proxmox-kernel-helper`, ZFS, `ifupdown2`, `proxmox-backup-client`, web toolkit, termproxy, journal reader and the rest of the server's Depends | PVE repo, which is built for `amd64 arm64` | Yes |
| `pbs-i18n`, `proxmox-backup-docs` | PBS repo. These are Architecture: all, but only listed in its amd64 index, so they're fetched through a throwaway apt config pointed at that index; apt checks the signed `InRelease` and the `.deb` hashes as usual. `proxmox-backup-docs` is pinned to the server's version. | Yes |
| `proxmox-backup-server` | qemus GitHub release, pinned by version + SHA-256 in `packages.py` (currently `4.2.8-1`) | **No** |

Of the 14 packages qemus ships, this needs only the one. Things to know:

- **The pin is trust-on-first-use.** qemus publishes no checksums (no
  SHA256SUMS file, and the GitHub API's per-asset digest is null for every
  asset). The pin guarantees every install gets the same bytes, not that
  they match Proxmox's source.
- **No apt updates for the three non-repo packages.** The installed
  arm64 server has no apt source for `proxmox-backup-server`, `pbs-i18n`
  or `proxmox-backup-docs`. Everything else updates normally from the PVE
  repo. Updating PBS itself means installing a newer qemus `.deb` by
  hand, and bumping `QEMUS_TAG`/`QEMUS_VERSION`/`QEMUS_SHA256` in
  `packages.py` for new installs.
- **A PBS host tracking the PVE repo** for shared packages is this
  project's arrangement, not something Proxmox documents.
- Inherited from Klargøring: arm64 Proxmox hosts are UEFI-only. Whether
  Klargøring's arm64 path has booted on real arm64 hardware is unverified;
  its README only claims amd64 end to end.

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
    repository.py        PBS (amd64) / PVE (arm64) apt repo + signing key
    packages.py          proxmox-backup install (arm64: + qemus server .deb),
                          kernel swap, os-prober removal
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
                        GitHub Release, amd64 + arm64 as a matrix

examples/
  answer.toml          generic template -- real per-site files are never
                        checked in here
```

## Building

### In CI (normal route)

Actions → "Build installer initrd + ISO" → Run workflow, or:

```
gh workflow run build.yml --repo knightmare2600/saekkevogn -f debian_suite=trixie
```

`workflow_dispatch` only (each run debootstraps from scratch, about 8
minutes). It builds both arches as a matrix — arm64 on GitHub's native
`ubuntu-24.04-arm` runner, not emulation — and publishes everything as
one GitHub Release tagged `saekkevogn-<date>-<suite>-<run-number>`, or
whatever tag you pass as `release_tag`. GitHub Releases are a flat
namespace, so assets get an arch suffix at publish time even though the
local build output uses per-arch folders.

### Locally: the initrd

```
sudo bash build/build-installer-initrd.sh /path/to/output-dir
# or, for arm64:
sudo ARCH=arm64 bash build/build-installer-initrd.sh /path/to/output-dir
```

Needs root and network access to deb.debian.org. Cross-building arm64 on
an amd64 host needs `qemu-user-static` (the script switches to
`qemu-debootstrap` itself). Produces, under `<output-dir>/<ARCH>/`:

- `installer-initrd.img`: the packed, xz-compressed initrd
- `vmlinuz`: the matching kernel
- `KERNEL_VERSION`: the kernel version string

The initrd is the same as Klargøring's apart from naming: a plain Debian
install environment. Nothing PBS-specific goes in until Stage 2 runs on
the target.

### Locally: the ISO

For sites where PXE isn't available. Run after the initrd build:

```
bash build/build-installer-iso.sh /path/to/initrd-output-dir /path/to/iso-output-dir
# or, for arm64:
ARCH=arm64 bash build/build-installer-iso.sh /path/to/initrd-output-dir /path/to/iso-output-dir
```

No root needed. Needs `xorriso` and `mtools`, plus `grub-pc-bin` +
`grub-efi-amd64-bin` for amd64 (a hybrid BIOS+UEFI ISO), or
`grub-efi-arm64-bin` for arm64 (UEFI only). `ARCH` must match the initrd
build. Produces `<output-dir>/<ARCH>/saekkevogn-installer.iso`.

## Booting via iPXE

The estate's `menu.ipxe` carries this (added in v2.16, arm64 in v2.17),
mirroring Klargøring's stanza and file layout. Its
`benarbejde/asset_manifest.json` fetches the release assets onto the web
server under these paths (asset names checked against
`saekkevogn-20261025-trixie-2`). The arm64 filenames are suffixed and the
amd64 ones aren't — the same asymmetric layout as Klargøring's:

```
:saekkevogn
iseq ${arch} arm64 && goto saekkevogn-arm64 || goto saekkevogn-amd64

:saekkevogn-amd64
kernel ${boot-url}/proxmox/saekkevogn/amd64/vmlinuz toml_url=${boot-url}/proxmox/${site-prefix}-pbs-answer.toml confirm-wipe no-reboot
initrd ${boot-url}/proxmox/saekkevogn/amd64/installer-initrd.img
boot

:saekkevogn-arm64
kernel ${boot-url}/proxmox/saekkevogn/arm64/vmlinuz-arm64 toml_url=${boot-url}/proxmox/${site-prefix}-pbs-answer.toml confirm-wipe no-reboot
initrd ${boot-url}/proxmox/saekkevogn/arm64/installer-initrd-arm64.img
boot
```

`${site-prefix}-pbs-answer.toml` is deliberately not
`${site-prefix}-answer.toml`, which is already Klargøring's PVE answer
file. The `-pbs-` naming is a presumption pending confirmation, and no
per-site `-pbs-answer.toml` file exists yet. As with every entry in that
menu, the site is resolved in iPXE (gateway-based detection setting
`${site-prefix}` and `${boot-url}`) — never hardcode one site's URL into
a shared menu entry.

## Booting via ISO

As with Klargøring, the GRUB menu (`build/grub.cfg`) ships four entries,
{local tty1, serial ttyS0 115200n8} × {auto-reboot (default), no-reboot},
all with a placeholder `toml_url=http://CHANGE-ME/answer.toml` and
`confirm-wipe`. Press `e`, replace `CHANGE-ME`, then boot with
`Ctrl-X`/`F10`. An unedited boot fails to fetch the TOML and exits
without touching any disk.

## Kernel cmdline options

- `toml_url=<url>`: **required**. Fetched with `curl -fsSL`, over plain
  HTTP in every example here. Bear that in mind if the file carries
  secrets (password hash, SSH keys) over an untrusted network.
- `confirm-wipe`: interactive/manual runs. An operator must confirm at
  the console before any disk is touched.
- `unattended`: production PXE use. Skips the confirmation.
- `no-reboot`: stay up after a successful install, for inspection.

**The installer refuses to touch any disk unless `confirm-wipe` or
`unattended` is present.** If neither is present, it logs the disks it
would wipe and exits. There is no interactive prompt to catch a missing
flag; the kernel cmdline is the entire interface.

Example production cmdline:

```
toml_url=http://192.168.139.50/proxmox/VRK-pbs-answer.toml unattended
```

## Debugging a booted instance

- Console autologin as root on `tty1` and `ttyS0`.
- SSH: `ssh root@<hostname-or-ip>`, password `saekkevogn`. Deliberately
  a documented password rather than a baked-in key, since the image is
  public. It applies only to the transient installer environment, never
  the installed target, which gets the TOML's password hash and SSH keys.
  Host keys are generated fresh per boot.
- Hostname is randomised per boot (`saekkevogn-<8 hex chars>`).
- `journalctl -u saekkevogn-installer --no-pager` for the installer's
  output (also in `/var/log/saekkevogn/install.log`), and
  `cat /run/answer.toml` for the fetched answer file.
- Wrong console keymap: `ckbcomp -layout gb | loadkeys -` (not
  `loadkeys gb` — Debian generates console keymaps from XKB data).
- After a `no-reboot` run the pool is exported, so `/target` is gone by
  design. Inspect with `zpool import -R /target rpool`, and
  `zpool export rpool` again before rebooting.

## The answer TOML

PBS's own automated installer uses the same answer-file format as PVE:
pve.proxmox.com/wiki/Automated_Installation covers PBS 3.2 onwards, lists
no PBS-specific keys, and uses kebab-case keys (snake_case is deprecated
from PBS 4.0). Sækkevogn uses that format, as a subset, with the same
contract as Klargøring, parsed field for field. See
`examples/answer.toml`. Currently supported:

- `disk-setup.filesystem = "zfs"` with `zfs.raid = "raid1"` across
  exactly 2 disks. `zfs.raid` is a TOML dotted key, so it parses as a
  nested `zfs` table; until 2026-10-22 both Sækkevogn and Klargøring
  looked it up as a literal `"zfs.raid"` key, never found it, and
  silently accepted any value as a mirror. Fixed in both (Klargøring
  `c1c9fd3`).
- `network.source = "from-dhcp"`
- `first-boot.source = "from-url"`

## Known limitations

- Untested on real hardware (see [Status](#status)).
- arm64: the PBS server package is unofficial and doesn't update via apt
  (see [arm64](#arm64)).
- No PBS datastore is created, and that is deliberate. The installed
  server comes up with only the ZFS root pool, and datastores are left for
  whoever runs the server to configure (the upstream answer format has no
  datastore keys either).
- Inherited from Klargøring: single disks, RAIDZ and static networking
  aren't implemented; no QEMU integration test harness; the initrd keeps
  `dkms`/`zfs-dkms`/build tooling installed after building the ZFS module.
