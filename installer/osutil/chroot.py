"""Bind-mount + chroot helper, and target-system configuration (hostname,
hosts, timezone, locale, keyboard, fstab, root password/SSH key injection).

Lives under osutil/ rather than os/, since installer/os/ would shadow the stdlib os module for the whole process
(main.py's own directory lands at sys.path[0] when run as a script, and a
subpackage literally named os/ sitting there wins over the real stdlib os
for every `import os` anywhere in the interpreter).
"""
import os
import subprocess

MOUNTS = ("proc", "sys", "dev", "dev/pts", "run")

# Only one of the apt-get calls across this codebase originally set
# DEBIAN_FRONTEND=noninteractive (the proxmox-ve install in Klargøring's
# proxmox/packages.py, the proxmox-backup install in Sækkevogn, now the PDM install here) -- every other chroot.run()'d apt-get call was
# exposed to hanging forever on a package's own interactive debconf
# prompt (confirmed: installing keyboard-configuration this way pops its
# own keyboard-layout dialog with no real terminal to answer it, and the
# installer just sits there with no output and no crash). Force it here,
# once, for every command Chroot.run() ever executes, rather than
# depending on each call site remembering to set it.
CHROOT_ENV = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}

# TOML has no explicit locale field (the Proxmox answer format's [global]
# has keyboard/country/fqdn/mailto/timezone, no locale) -- derive a sane default from
# country. Extend as real sites need it.
LOCALE_BY_COUNTRY = {
    "gb": "en_GB.UTF-8",
    "us": "en_US.UTF-8",
}


class Chroot:
    def __init__(self, target, log):
        self.target = target
        self.log = log

    def __enter__(self):
        self.log.info(f"bind-mounting proc/sys/dev/run into {self.target}")
        subprocess.run(["mount", "-t", "proc", "proc", f"{self.target}/proc"], check=True)
        subprocess.run(["mount", "-t", "sysfs", "sysfs", f"{self.target}/sys"], check=True)
        subprocess.run(["mount", "--bind", "/dev", f"{self.target}/dev"], check=True)
        subprocess.run(["mount", "-t", "devpts", "devpts", f"{self.target}/dev/pts"], check=True)
        # Without this, tools run inside the chroot (proxmox-boot-tool,
        # blkid, lsblk) read a separate, empty /target/run instead of the
        # live udev database at /run/udev/data/ -- confirmed: a real run
        # had partprobe/udevadm settle refresh the *outer* /run correctly,
        # but proxmox-boot-tool init still saw stale/empty partition info
        # querying from inside the chroot, because nothing connected the
        # two. udevadm itself still refuses to do anything from inside a
        # chroot (a separate, deliberate safety check, not fixed by this),
        # but reading already-updated database files under /run doesn't
        # need that -- it just needs to see the same /run.
        subprocess.run(["mount", "--bind", "/run", f"{self.target}/run"], check=True)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.log.info(f"unmounting {self.target}/{{{','.join(MOUNTS)}}}")
        for m in reversed(MOUNTS):
            subprocess.run(["umount", "-lf", f"{self.target}/{m}"], check=False)
        return False

    def run(self, args, **kwargs):
        kwargs.setdefault("check", True)
        kwargs.setdefault("env", CHROOT_ENV)
        return subprocess.run(["chroot", self.target, *args], **kwargs)


def _primary_ipv4_line():
    # "2: ens18    inet 192.0.2.10/24 ..." -- field 1 is the interface,
    # field 3 the address. Shared by primary_ipv4() and primary_interface()
    # so both always describe the same NIC.
    result = subprocess.run(
        ["ip", "-4", "-o", "addr", "show", "scope", "global"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip().splitlines()[0].split()


def primary_ipv4(log):
    ip = _primary_ipv4_line()[3].split("/")[0]
    log.info(f"using {ip} as this host's non-loopback address for /etc/hosts")
    return ip


def primary_interface(log):
    iface = _primary_ipv4_line()[1]
    log.info(f"using {iface} (the installer's own DHCP-configured NIC) as the target's primary interface")
    return iface


def write_hostname(target, fqdn):
    hostname = fqdn.split(".")[0]
    with open(f"{target}/etc/hostname", "w") as f:
        f.write(hostname + "\n")
    return hostname


def write_hosts(target, fqdn, hostname, ip, log):
    # Proxmox requires the FQDN to resolve to a non-loopback address
    # (verified against pve.proxmox.com/wiki/Install_Proxmox_VE_on_Debian_13_Trixie)
    log.info(f"writing /etc/hosts: {ip} {fqdn} {hostname}")
    with open(f"{target}/etc/hosts", "w") as f:
        f.write("127.0.0.1\tlocalhost\n")
        f.write(f"{ip}\t{fqdn} {hostname}\n")
        f.write("\n::1\t\tlocalhost ip6-localhost ip6-loopback\n")


def write_timezone(chroot, timezone, log):
    log.info(f"setting timezone {timezone}")
    with open(f"{chroot.target}/etc/timezone", "w") as f:
        f.write(timezone + "\n")
    chroot.run(["ln", "-sf", f"/usr/share/zoneinfo/{timezone}", "/etc/localtime"])
    chroot.run(["dpkg-reconfigure", "-f", "noninteractive", "tzdata"])


def write_locale(chroot, country, log):
    # A plain debootstrap doesn't include locales -- locale-gen doesn't
    # exist until the package that provides it is installed (confirmed by
    # a real run: "chroot: failed to run command 'locale-gen': No such
    # file or directory" -- exit 127, package missing, not a PATH issue).
    log.info("installing locales package into target")
    chroot.run(["apt-get", "update"])
    chroot.run(["apt-get", "install", "-y", "locales"])

    locale = LOCALE_BY_COUNTRY.get(country, "en_US.UTF-8")
    # en_US.UTF-8 is generated regardless of the system's own default,
    # alongside it -- confirmed on a real install: bash on login warned
    # "setlocale: LC_ALL: cannot change locale (en_US.UTF-8): No such file
    # or directory" three times, almost certainly the connecting client's
    # own LC_* env vars being forwarded (e.g. over SSH) and finding nothing
    # generated to satisfy them. LANG itself still defaults to the
    # TOML-derived locale below; this only avoids that warning for anyone
    # connecting from a client with the (very common) en_US.UTF-8 default.
    to_generate = {locale, "en_US.UTF-8"}
    log.info(f"setting locale {locale} (derived from country={country!r}); "
             f"also generating {sorted(to_generate - {locale})} for client-forwarded LC_*")
    with open(f"{chroot.target}/etc/locale.gen", "a") as f:
        for loc in sorted(to_generate):
            f.write(f"{loc} UTF-8\n")
    chroot.run(["locale-gen"])
    with open(f"{chroot.target}/etc/default/locale", "w") as f:
        f.write(f'LANG="{locale}"\n')


def write_keyboard(chroot, keyboard, log):
    # Same class of bug as write_locale: keyboard-configuration isn't part
    # of a plain debootstrap either. Install it explicitly rather than
    # assume it, and don't swallow a real failure with check=False now that
    # the actual precondition (package present) is being met.
    log.info("installing keyboard-configuration package into target")
    chroot.run(["apt-get", "install", "-y", "keyboard-configuration"])

    # TOML's keyboard value looks like "en-gb" -- XKBLAYOUT wants just the
    # layout code ("gb"), not the locale-style prefix.
    layout = keyboard.split("-")[-1]
    log.info(f"setting XKBLAYOUT={layout} (from keyboard={keyboard!r})")
    with open(f"{chroot.target}/etc/default/keyboard", "w") as f:
        f.write(f'XKBMODEL="pc105"\nXKBLAYOUT="{layout}"\nXKBVARIANT=""\nXKBOPTIONS=""\n')
    chroot.run(["dpkg-reconfigure", "-f", "noninteractive", "keyboard-configuration"])


def write_network(target, log):
    # PBS/PDM DIFFERENCE (2026-10-22, from Sækkevogn; re-checked for PDM
    # 2026-10-25), read this first: everything in the next
    # two comments was observed on PVE in Klargøring. The "inet manual"
    # stanza and interfaces.new both come from ifupdown2's postinst
    # proxmox_compatibility() (checked in ifupdown2_3.3.0-1+pmx12's own
    # postinst), which begins with `exit if !$haspve_common` -- it only
    # does anything when PVE::INotify (libpve-common-perl, PVE-only) is
    # installed. On PBS it exits immediately, and on PDM too: nothing in
    # PDM's dependency tree pulls in libpve-common-perl (checked against
    # the PDM, PVE arm64 and qemus package metadata, 2026-10-25), so the
    # in-place edit below finds nothing to change and the target would
    # boot with no network configured at all. The fallback at the end of this function covers
    # that: it writes a DHCP stanza for the NIC this installer itself got
    # its lease on. The in-place edit is kept so the PVE-shaped case still
    # behaves exactly as it did in Klargøring.
    #
    # proxmox-ve's own postinst (installed in proxmox/packages.py, which
    # must run before this) generates /etc/network/interfaces itself --
    # confirmed on a real install: an "autogenerated" header explicitly
    # saying "Please do NOT modify this file directly, unless you know
    # what you're doing" / "PVE will preserve these directives", with the
    # real NIC left as "iface ensXX inet manual" (a template awaiting
    # explicit config, e.g. as a bridge port) rather than actually brought
    # up. Edit that file in place -- flip only the "manual" method to
    # "dhcp" for real (non-lo) interfaces, adding "auto <iface>" only if
    # missing (needed for ifupdown2 to actually activate it at boot) --
    # rather than regenerating the whole file and discarding Proxmox's
    # own header/structure for a change that only ever needed to be a
    # one-line substitution.
    path = f"{target}/etc/network/interfaces"

    # ifupdown2's own postinst (installed explicitly in packages.install()
    # here; a proxmox-ve dependency in Klargøring) detects the
    # debootstrap-default interfaces file needs migrating to its syntax
    # (on PVE only -- see top of function) and stages the migrated version
    # at interfaces.new ("saved ... for hot-apply or next reboot") rather
    # than writing it live -- confirmed on a real install: interfaces
    # still had only lo, while interfaces.new had PVE's real header and
    # the "iface ensXX inet manual" stanza this function needs to edit.
    # Reading interfaces alone found nothing every time and silently
    # edited the wrong file. Promote it before reading, if present.
    new_path = f"{path}.new"
    if os.path.exists(new_path):
        log.info(f"{new_path} exists (ifupdown2 migration) -- promoting over {path}")
        os.replace(new_path, path)

    lines = []
    if os.path.exists(path):
        with open(path) as f:
            lines = f.readlines()

    autos = {line.split()[1] for line in lines if line.startswith("auto ")}
    changed = []
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("iface ") and stripped.endswith("inet manual"):
            name = stripped.split()[1]
            if name != "lo":
                if name not in autos:
                    out.append(f"auto {name}\n")
                    autos.add(name)
                line = line.replace("inet manual", "inet dhcp")
                changed.append(name)
        out.append(line)

    if changed:
        log.info(f"{path}: {changed} inet manual -> inet dhcp")
        with open(path, "w") as f:
            f.writelines(out)
        return

    configured = {line.split()[1] for line in lines
                  if line.strip().startswith("iface ") and line.split()[1] != "lo"}
    if configured:
        log.info(f"no 'inet manual' interfaces in {path}, but {sorted(configured)} "
                 "already configured -- leaving it alone")
        return

    # The PBS/PDM case (see top of function). The installer runs on the same
    # hardware and kernel family as the target, with the same udev
    # predictable-naming scheme, so the NIC name seen here is the one the
    # installed system will see too. Unverified on real PBS or PDM hardware yet.
    iface = primary_interface(log)
    log.info(f"no non-lo interfaces in {path} -- adding auto {iface} / inet dhcp")
    if not lines:
        lines = ["auto lo\n", "iface lo inet loopback\n"]
    if lines[-1].strip():
        lines.append("\n")
    lines += [f"auto {iface}\n", f"iface {iface} inet dhcp\n"]
    with open(path, "w") as f:
        f.writelines(lines)


def write_fstab(target, log):
    # ZFS root is not fstab-managed (zfs-mount.service/zpool cache handle
    # it). Nor, it turns out, is the ESP: a real reference PVE install's
    # /etc/fstab (checked 2026-07-24) has no /boot/efi entry at all --
    # proxmox-boot-tool (see boot/grub.py) mounts each ESP itself only
    # when it needs to format/init/refresh it, not permanently. An
    # earlier version of this function added a persistent UUID-based
    # /boot/efi mount here, which doesn't match that real convention.
    log.info("writing minimal fstab (no /boot/efi entry -- proxmox-boot-tool "
             "mounts each ESP itself only when it needs to, matching a real "
             "PVE install's own /etc/fstab)")
    with open(f"{target}/etc/fstab", "w") as f:
        f.write("# /etc/fstab: static file system information.\n")
        f.write("proc  /proc  proc  defaults  0  0\n")


def inject_root_password(target, password_hash, log):
    log.info("injecting root password hash into /etc/shadow")
    shadow_path = f"{target}/etc/shadow"
    with open(shadow_path) as f:
        lines = f.readlines()
    with open(shadow_path, "w") as f:
        for line in lines:
            if line.startswith("root:"):
                fields = line.split(":")
                fields[1] = password_hash
                f.write(":".join(fields))
            else:
                f.write(line)


def inject_ssh_keys(target, ssh_keys, log):
    log.info(f"injecting {len(ssh_keys)} root SSH key(s)")
    ssh_dir = f"{target}/root/.ssh"
    subprocess.run(["install", "-d", "-m", "700", ssh_dir], check=True)
    auth_keys = f"{ssh_dir}/authorized_keys"
    with open(auth_keys, "w") as f:
        for key in ssh_keys:
            f.write(key.strip() + "\n")
    subprocess.run(["chmod", "600", auth_keys], check=True)
