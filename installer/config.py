"""Loads and validates the Proxmox-compatible answer TOML.

Per plan.txt: parse this directly, don't translate it into some other
internal schema. Downstream modules read straight out of the dict this
returns (e.g. config["global"]["fqdn"]).
"""
import tomllib

REQUIRED = {
    "global": ("keyboard", "country", "fqdn", "timezone", "root-password-hashed", "root-ssh-keys"),
    "network": ("source",),
    "disk-setup": ("filesystem", "disk-list"),
    "first-boot": ("source", "url"),
}


# zfs.raid level -> the exact number of disks this installer accepts for
# it. raid1 is the standard build. raid0 with one disk is the degraded
# build, replicating the estate's PVE <SITE>-degraded.toml (same answer
# file, zfs.raid = "raid0", disk-list = ["sda"]): single-disk, NOT
# production ready. raid0 striped across several disks is deliberately not
# offered -- nothing in the estate asks for it.
RAID_DISK_COUNT = {"raid1": 2, "raid0": 1}


class ConfigError(Exception):
    pass


def load(path):
    with open(path, "rb") as f:
        config = tomllib.load(f)
    validate(config)
    return config


def validate(config):
    for section, keys in REQUIRED.items():
        if section not in config:
            raise ConfigError(f"missing [{section}] section")
        for key in keys:
            if key not in config[section]:
                raise ConfigError(f"missing {key} in [{section}]")

    disks = config["disk-setup"]["disk-list"]
    if not isinstance(disks, list) or not disks:
        raise ConfigError("disk-setup.disk-list must be a non-empty list")

    if config["disk-setup"]["filesystem"] != "zfs":
        raise ConfigError(
            f"filesystem={config['disk-setup']['filesystem']!r} not supported yet (only 'zfs')"
        )

    # `zfs.raid = "raid1"` is a TOML dotted key: tomllib parses it as a
    # nested table, {"zfs": {"raid": "raid1"}}, never a literal "zfs.raid"
    # key -- the earlier .get("zfs.raid", ...) (inherited from Klargøring)
    # never matched and always fell back to its default, so a TOML asking
    # for e.g. raidz1 passed validation and silently got a mirror.
    raid = zfs_raid(config)
    if raid not in RAID_DISK_COUNT:
        raise ConfigError(f"zfs.raid={raid!r} not supported yet (only {sorted(RAID_DISK_COUNT)})")
    if len(disks) != RAID_DISK_COUNT[raid]:
        raise ConfigError(f"zfs.raid={raid} needs exactly {RAID_DISK_COUNT[raid]} disk(s), "
                          f"got {disks!r}")

    if config["network"]["source"] != "from-dhcp":
        raise ConfigError(
            f"network.source={config['network']['source']!r} not supported yet (only 'from-dhcp')"
        )


def zfs_raid(config):
    """The answer file's zfs.raid, defaulting to raid1 (the default
    inherited from Klargøring) -- the one place this is read, for
    validate() and for storage/zfs.py alike."""
    return config["disk-setup"].get("zfs", {}).get("raid", "raid1")
