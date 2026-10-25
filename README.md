# Pakhus

An automated bare-metal installer for **Proxmox Datacenter Manager**, on
**amd64 and arm64**: PXE/iPXE boot a custom-built initrd, feed it a
Proxmox-compatible answer TOML over HTTP, and it debootstraps Debian Trixie
onto a ZFS mirror, installs PDM, and reboots into a working datacentre
manager — no ISO in the loop, no Debian Installer `partman`, no manual
intervention.

## The name

*Pakhus* is Danish for **warehouse** (literally "pack-house"): the
building where everything gets stored, counted and kept track of. That is
roughly the job PDM does for an estate's PVE clusters and PBS servers, so
it completes the set:

| Project | Danish | English | Installs |
|---|---|---|---|
| [Klargøring](https://github.com/knightmare2600/klargoring) | klargøring | preparation, making ready | Proxmox VE |
| [Sækkevogn](https://github.com/knightmare2600/saekkevogn) | sækkevogn | sack truck | Proxmox Backup Server |
| Pakhus | pakhus | warehouse | Proxmox Datacenter Manager |

Unlike its siblings, *pakhus* has no æ/ø/å in it, so internal names
(`pakhus-installer.service`, `/opt/pakhus/`, the `pakhus-<hex>` hostname
prefix, the ISO filename) need no ASCII respelling.

A fine example of the genre is [Storms Pakhus](https://stormspakhus.dk/en/)
in Odense: per its own history page, a warehouse built around 1942 that
stored timber, then packaging for Eric Storm (whose name it still
carries), stood empty for a few years, and reopened in September 2017 as
the city's largest street-food market. The food is excellent, and it is
about 300 m from Odense Station (straight line, per OpenStreetMap), so
it's the obvious place to wait out a delayed DSB train while an arm64 PDM
install runs. Its brief as a street-food market was to keep "the
warehouse's soul and history", and so is this repo's: it is Sækkevogn
with the stock swapped.

## Status

| | amd64 | arm64 |
|---|---|---|
| Initrd + kernel + ISO build (CI) | ✅ `pakhus-20261025-trixie-1` | ✅ `pakhus-20261025-trixie-1` |
| Install on real hardware | ❌ not yet tested | ❌ not yet tested |
| PDM server packages | official (PDM repo) | **unofficial** (qemus, see [arm64](#arm64)) |

This repo started as a clone of Sækkevogn (its full history is kept —
everything up to and including `7396e95` is Sækkevogn's, and up to
`69df4ac` Klargøring's) with PDM-specific changes on top. None of
Klargøring's or Sækkevogn's verification carries over automatically.
What *has* been verified for Pakhus (2026-10-25):

- **Builds:** GitHub Actions run `40183728834` (2026-10-25, 9 minutes)
  built the initrd, kernel and ISO for both arches and published release
  `pakhus-20261025-trixie-1`. The installer kernel is Debian's
  `6.12.107+deb13` on both.
- **Repo and packages:** the PDM repo URI/suite/component and metapackage
  name, against pdm.proxmox.com/docs/installation.html and the live repo's
  own `Release`/`Packages` indexes. The PDM docs publish the same Proxmox
  archive keyring SHA-256 that Klargøring and Sækkevogn already pin.
- **Dependency closure:** starting from everything the installer asks
  for, the full Depends + Pre-Depends + Recommends closure (every
  alternative) resolves by name on both arches — amd64 against Debian
  trixie + the PDM repo, arm64 against Debian trixie + the PVE arm64 repo
  + the qemus and PDM `Architecture: all` packages. The only unresolved
  names are dead alternative branches (`hurd`, `file-rc`, …) and, on
  arm64, the expected `proxmox-datacenter-manager-meta` Recommends.
- **Network fallback still needed:** `libpve-common-perl` is not in either
  closure, so ifupdown2's PVE compatibility hook exits early on PDM
  exactly as on PBS (see [The network fallback](#the-network-fallback)).
- **arm64 packages:** the three qemus `.deb`s downloaded, SHA-256 pinned,
  and each pin matches GitHub's own published digest for the asset. The
  qemus UI package's payload is byte-identical to Proxmox's official one.
  The installer's own fetch functions ran end to end against the real
  sources in a scratch harness: all three qemus `.deb`s downloaded and
  pin-checked, a wrong pin refused, `pdm-i18n` +
  `proxmox-datacenter-manager-docs` 1.1.9 + `proxmox-geojson-data`
  fetched through apt's own signature checking, temporary apt state
  cleaned up.

Not yet verified: a real install and first boot on either arch, and
apt's version-constraint resolution on a real arm64 system.

## Why this exists

Same reason as its siblings: Proxmox's automated-install mechanism is
ISO-based, with no supported way to PXE-boot its installer and point it at
an answer file over HTTP. gyptazy's suggestion for PVE — PXE-boot plain
Debian and layer the Proxmox repo and packages on afterwards (see
Klargøring's README for the full quote) — applies to PDM just as well.
Pakhus does that with the same TOML contract, so PVE, PBS and PDM hosts
in an estate are all built the same way.

## Getting it

Ready-built files are on the
[Releases](https://github.com/knightmare2600/pakhus/releases) page
(latest: `pakhus-20261025-trixie-1`), one set per arch:

| Asset | What it is |
|---|---|
| `vmlinuz-<arch>` | installer kernel |
| `installer-initrd-<arch>.img` | installer initrd (~230MB amd64, ~207MB arm64) |
| `KERNEL_VERSION-<arch>` | the kernel's version string |
| `pakhus-installer-<arch>.iso` | both of the above wrapped in a bootable ISO |

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
   root password hash + SSH key injection, the Proxmox repo + PDM
   packages (see [Architectures](#architectures) for where each comes
   from), network config, `proxmox-boot-tool`-managed bootloader setup,
   and a `first-boot.service` that fetches and runs a site-specific script
   on the real first boot.
5. The ZFS pool is exported cleanly and the machine reboots into PDM
   (web UI on **port 8443**, per the PDM docs).

## What differs from Sækkevogn

| Area | Sækkevogn (PBS) | Pakhus (PDM) | Why |
|---|---|---|---|
| apt repo | amd64: `debian/pbs`, `pbs-no-subscription`. arm64: `debian/pve` | amd64: `debian/pdm`, `pdm-no-subscription`. arm64: `debian/pve`, `pve-no-subscription` | PDM docs; arm64 see below |
| Main package | `proxmox-backup` | amd64: `proxmox-datacenter-manager-meta`. arm64: its Depends one by one | `-meta` Depends on `proxmox-default-kernel` and `proxmox-kernel-helper` (`proxmox-boot-tool`); `-container-meta` keeps the Debian kernel and pulls in neither |
| arm64 unofficial packages | 1 (server) | 3 (server, client, UI) | All three are built or repackaged by qemus; see below |
| arm64 `Architecture: all` from the amd64 index | `pbs-i18n`, `proxmox-backup-docs` | `pdm-i18n`, `proxmox-datacenter-manager-docs`, `proxmox-geojson-data` | None are in the PVE arm64 index |
| arm64 qemus pin | trust-on-first-use (no digests published) | cross-checked against GitHub's per-asset digest | qemus's PDM releases carry digests |
| Web UI | port 8007 | port 8443 | Upstream defaults |
| `ifupdown2`, network fallback, `open-iscsi` dropped | as Sækkevogn | unchanged | PDM also only Recommends `ifupdown2` and has no PVE perl |

### The network fallback

Inherited unchanged from Sækkevogn. On PVE, `/etc/network/interfaces`
gets an `iface <nic> inet manual` stanza from ifupdown2's postinst
`proxmox_compatibility()`, which Klargøring then flips to `dhcp`. That
function starts with `exit if !$haspve_common`, so it only does anything
when `libpve-common-perl` is installed. Neither PBS nor PDM installs it
(checked for PDM via the dependency closure above), so `write_network()`
adds a DHCP stanza for the NIC the installer itself got its lease on,
whenever no non-`lo` interface is configured. That relies on the target
seeing the same NIC name as the installer — unverified on real hardware.

## Architectures

amd64 and arm64, with the same build setup as its siblings
(`ARCH=amd64|arm64`, `<output-dir>/<ARCH>/`, native `ubuntu-24.04-arm` CI
runner). The initrd is plain Debian on both; everything PDM-specific
happens at install time, on the target.

### arm64

**arm64 PDM comes from [qemus/proxmox-datacenter-arm64](https://github.com/qemus/proxmox-datacenter-arm64),
not from Proxmox.** Proxmox's own PDM trixie repo is amd64-only: its
`Release` file lists `Architectures: amd64`, and `binary-arm64/` returns
404 (checked 2026-10-25). qemus — the PDM counterpart of the qemus PBS
build Sækkevogn uses, and likewise "not affiliated, sponsored, or endorsed
by Proxmox Server Solutions GmbH" — publishes `.deb`s and a `build.sh install` helper, but no bare-metal
installer, so Pakhus wires its packages into the install. On arm64,
`proxmox/packages.py` installs from three sources:

| Package(s) | Source | Official? |
|---|---|---|
| Kernel, `proxmox-kernel-helper`, ZFS, `ifupdown2`, `proxmox-termproxy`, `proxmox-mini-journalreader`, `pve-xtermjs`, `novnc-pve`, `libproxmox-acme-plugins` and the rest | PVE repo, built for `amd64 arm64` | Yes |
| `pdm-i18n`, `proxmox-datacenter-manager-docs`, `proxmox-geojson-data` | PDM repo, via a throwaway apt config pointed at its amd64 index (apt checks the signed `InRelease` and the `.deb` hashes as usual). The docs are pinned to the server's version. | Yes |
| `proxmox-datacenter-manager`, `proxmox-datacenter-manager-client`, `proxmox-datacenter-manager-ui` | qemus GitHub release, each pinned by SHA-256 in `packages.py` (currently `1.1.9`, UI `1.1.4`) | **No** |

Of the 13 packages in qemus's `1.1.9` release, this needs three. Things
to know:

- **The UI is official bytes in an unofficial wrapper.** Proxmox ships
  `proxmox-datacenter-manager-ui` as `Architecture: amd64`; qemus
  republishes it as `all`. Its payload and `md5sums` are identical to the
  official `1.1.4` `.deb`; only the control file's `Architecture` line
  differs.
- **qemus's server drops a dependency.** Official `1.1.9` Depends on
  `libproxmox-acme-plugins`; qemus's build of `1.1.9` doesn't (its
  `build.sh` deletes that name from `debian/control` along with
  build-only entries). Pakhus installs it explicitly so ACME DNS plugins
  are present. The other differences (`libbz2-1.0`, `libzstd1`) are
  auto-generated shared-library dependencies.
- **The pins prove provenance from qemus, not from Proxmox.** GitHub's
  digest confirms the bytes are what qemus uploaded; nothing confirms
  they match Proxmox's source.
- **No apt updates for the six non-repo packages.** The installed arm64
  host has no apt source for the three qemus packages or the three
  `Architecture: all` ones. Everything else updates normally from the PVE
  repo. Updating PDM itself means installing newer qemus `.deb`s by hand
  (qemus's own `build.sh install` does this), and bumping `QEMUS_TAG`,
  `QEMUS_VERSION` and the hashes in `QEMUS_DEBS` for new installs.
- **A PDM host tracking the PVE repo** for shared packages is this
  project's arrangement, not something Proxmox documents.
- Inherited: arm64 Proxmox hosts are UEFI-only, and whether this family's
  arm64 path has booted on real arm64 hardware is unverified.

## Repository layout

```
installer/          the Stage 2 installer, runs inside the booted initrd
  main.py              orchestrator: load TOML -> ... -> reboot
  config.py            TOML load + validation
  logger.py            logs to /var/log/pakhus/install.log + console
  storage/
    detect.py            disk detection/validation (size floor, not mounted)
    partition.py         sgdisk GPT layout: bios_boot + ESP + zfs, per disk
    zfs.py               zpool/zfs dataset creation
  osutil/              (not os/ -- would shadow the stdlib os module)
    debootstrap.py       debootstrap onto /target
    chroot.py            bind-mount/chroot helper + target config
  proxmox/
    repository.py        PDM (amd64) / PVE (arm64) apt repo + signing key
    packages.py          proxmox-datacenter-manager-meta install (arm64:
                          + qemus .debs), kernel swap, os-prober removal
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
gh workflow run build.yml --repo knightmare2600/pakhus -f debian_suite=trixie
```

`workflow_dispatch` only (each run debootstraps from scratch, about 9
minutes). It builds both arches as a matrix — arm64 on GitHub's native
`ubuntu-24.04-arm` runner, not emulation — and publishes everything as
one GitHub Release tagged `pakhus-<date>-<suite>-<run-number>`, or
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

The initrd is the same as Sækkevogn's apart from naming: a plain Debian
install environment. Nothing PDM-specific goes in until Stage 2 runs on
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
build. Produces `<output-dir>/<ARCH>/pakhus-installer.iso`.

## Booting via iPXE

The estate's `menu.ipxe` carries this (added in v2.18, replacing the old
`:proxmox-dcm` placeholder), mirroring Sækkevogn's stanza, including its
asymmetric layout: arm64 filenames suffixed, amd64 ones not. Its
`benarbejde/asset_manifest.json` fetches the release assets onto the web
server under these paths (asset names checked against
`pakhus-20261025-trixie-1`):

```
:pakhus
iseq ${arch} arm64 && goto pakhus-arm64 || goto pakhus-amd64

:pakhus-amd64
kernel ${boot-url}/proxmox/pakhus/amd64/vmlinuz toml_url=${boot-url}/proxmox/${site-prefix}-pdm-answer.toml confirm-wipe no-reboot
initrd ${boot-url}/proxmox/pakhus/amd64/installer-initrd.img
boot

:pakhus-arm64
kernel ${boot-url}/proxmox/pakhus/arm64/vmlinuz-arm64 toml_url=${boot-url}/proxmox/${site-prefix}-pdm-answer.toml confirm-wipe no-reboot
initrd ${boot-url}/proxmox/pakhus/arm64/installer-initrd-arm64.img
boot
```

`${site-prefix}-pdm-answer.toml` follows Sækkevogn's `-pbs-` convention;
the `-pdm-` naming is a presumption pending confirmation, and no per-site
`-pdm-answer.toml` file exists yet. As with every entry in that menu, the
site is resolved in iPXE (gateway-based detection setting `${site-prefix}`
and `${boot-url}`) — never hardcode one site's URL into a shared menu
entry.

## Booting via ISO

As with its siblings, the GRUB menu (`build/grub.cfg`) ships four entries,
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
toml_url=http://192.168.139.50/proxmox/VRK-pdm-answer.toml unattended
```

## Debugging a booted instance

- Console autologin as root on `tty1` and `ttyS0`.
- SSH: `ssh root@<hostname-or-ip>`, password `pakhus`. Deliberately a
  documented password rather than a baked-in key, since the image is
  public. It applies only to the transient installer environment, never
  the installed target, which gets the TOML's password hash and SSH keys.
  Host keys are generated fresh per boot.
- Hostname is randomised per boot (`pakhus-<8 hex chars>`).
- `journalctl -u pakhus-installer --no-pager` for the installer's output
  (also in `/var/log/pakhus/install.log`), and `cat /run/answer.toml` for
  the fetched answer file.
- Wrong console keymap: `ckbcomp -layout gb | loadkeys -` (not
  `loadkeys gb` — Debian generates console keymaps from XKB data).
- After a `no-reboot` run the pool is exported, so `/target` is gone by
  design. Inspect with `zpool import -R /target rpool`, and
  `zpool export rpool` again before rebooting.

## The answer TOML

The same answer-file format and subset as Klargøring and Sækkevogn,
parsed field for field. See `examples/answer.toml`. Whether Proxmox's own
automated installer formally covers PDM with this format is unverified;
Pakhus doesn't depend on that, since it parses the file itself. Currently
supported:

- `disk-setup.filesystem = "zfs"` with `zfs.raid = "raid1"` across
  exactly 2 disks (the dotted-key fix from Sækkevogn/Klargøring
  `c1c9fd3` is included).
- `network.source = "from-dhcp"`
- `first-boot.source = "from-url"`

## Known limitations

- Untested on real hardware (see [Status](#status)).
- arm64: the PDM server, client and UI packages are unofficial and don't
  update via apt (see [arm64](#arm64)).
- No remotes are added, and that is deliberate. The installed PDM comes
  up with no PVE or PBS remotes configured; adding them (which needs
  credentials or API tokens for each) is left to whoever runs it, or to
  the site's first-boot script.
- Inherited: single disks, RAIDZ and static networking aren't
  implemented; no QEMU integration test harness; the initrd keeps
  `dkms`/`zfs-dkms`/build tooling installed after building the ZFS module.
