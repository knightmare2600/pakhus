"""The confirm-wipe prompt: before any disk is touched, show the operator
exactly which disks are about to be erased and require them to type YES.

This runs as a systemd service with no terminal of its own, while the
initrd's build script (build/build-installer-initrd.sh) gives tty1 and
ttyS0 an autologin root shell via getty@tty1 and serial-getty@ttyS0.
Reading from those terminals while a shell is also reading them would
split the operator's keystrokes between the two, so the gettys are
stopped for the duration of the prompt and started again afterwards.
The prompt goes to both consoles and the first answer from either wins;
a console that can't be opened (e.g. no serial port) is skipped.

Stopping a getty hangs its terminal up asynchronously: `systemctl stop`
returning doesn't mean the hangup has happened. Confirmed on a real boot
(2026-11-01): tcgetattr() on a freshly opened console failed with EIO, and
because termios.error is not an OSError the installer crashed before
showing any prompt. So opening retries while the terminal settles, and a
console that hangs up after the prompt is shown gets reopened and asked
again rather than being treated as gone.

Anything other than exactly YES -- including no usable console at all --
is a refusal, and nothing is touched.
"""
import fcntl
import os
import select
import subprocess
import termios
import time

# (device, getty unit) pairs -- the same two consoles, and the same unit
# names, that build/build-installer-initrd.sh sets up its autologin on.
CONSOLES = (("/dev/tty1", "getty@tty1.service"), ("/dev/ttyS0", "serial-getty@ttyS0.service"))

ANSWER = "YES"

# How long a console gets to settle after its getty stops, and how many
# late hangups are tolerated before giving up on that console.
OPEN_ATTEMPTS = 6
OPEN_DELAY = 0.5
MAX_REOPENS = 5


def _prompt_text(disks, raid):
    rows = "\n".join(f"     {d['path']:<14} {d['size'] / 1024**3:7.1f}GiB  {d['model']}" for d in disks)
    return (
        "\n\n"
        "=====================================================================\n"
        f"  confirm-wipe: about to ERASE these disks ({raid}) and install:\n"
        f"{rows}\n"
        "\n"
        f"  Type {ANSWER} and press Enter to wipe them.\n"
        "  Anything else aborts without touching any disk.\n"
        "=====================================================================\n"
        "> "
    )


def _open_console(path):
    # O_NONBLOCK only for the open itself, so a serial port waiting for
    # carrier can't hang the prompt; reads then block as normal.
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        fcntl.fcntl(fd, fcntl.F_SETFL, fcntl.fcntl(fd, fcntl.F_GETFL) & ~os.O_NONBLOCK)
        # The killed autologin shell may have left the terminal in raw mode;
        # put it back to line-at-a-time input with echo so the operator sees
        # what they type and os.read() returns a whole line.
        attrs = termios.tcgetattr(fd)
        attrs[0] |= termios.ICRNL
        attrs[3] |= termios.ICANON | termios.ECHO | termios.ECHOE
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        termios.tcflush(fd, termios.TCIFLUSH)
    except (OSError, termios.error):
        os.close(fd)
        raise
    return fd


def _open_settled(path, log):
    """A usable fd for this console, or None once it has had time to settle."""
    for attempt in range(OPEN_ATTEMPTS):
        if attempt:
            time.sleep(OPEN_DELAY)
        try:
            return _open_console(path)
        except (OSError, termios.error) as e:
            failure = e
    log.info(f"confirm-wipe: {path} unusable ({failure}), skipping it")
    return None


def _send(fd, data):
    # A write can hit the same hangup; the read loop deals with that.
    try:
        os.write(fd, data)
    except OSError:
        pass


def ask(disks, raid, log):
    """True only if an operator typed exactly YES on one of the consoles."""
    units = [unit for _, unit in CONSOLES]
    log.info(f"confirm-wipe: stopping {units} so the prompt owns the consoles")
    subprocess.run(["systemctl", "stop", *units], check=False)

    text = _prompt_text(disks, raid).encode()
    reopens = {path: 0 for path, _ in CONSOLES}
    fds = {}
    try:
        for path, _ in CONSOLES:
            fd = _open_settled(path, log)
            if fd is not None:
                fds[fd] = path
                _send(fd, text)
        if not fds:
            log.error("confirm-wipe: no usable console to ask on -- refusing to wipe")
            return False
        log.info(f"confirm-wipe: waiting for {ANSWER} on {sorted(fds.values())}")

        while fds:
            ready, _, _ = select.select(list(fds), [], [])
            for fd in ready:
                try:
                    line = os.read(fd, 1024)
                except OSError:
                    line = b""
                if not line:
                    path = fds.pop(fd)
                    os.close(fd)
                    if reopens[path] < MAX_REOPENS:
                        reopens[path] += 1
                        log.info(f"confirm-wipe: {path} hung up, reopening it")
                        new_fd = _open_settled(path, log)
                        if new_fd is not None:
                            fds[new_fd] = path
                            _send(new_fd, text)
                            continue
                    log.info(f"confirm-wipe: {path} closed, no longer listening on it")
                    continue
                answer = line.decode(errors="replace").strip()
                ok = answer == ANSWER
                verdict = (b"\n  Confirmed -- wiping and installing.\n\n" if ok
                           else b"\n  Not confirmed -- aborting, no disk touched.\n\n")
                for other in fds:
                    _send(other, verdict)
                log.info(f"confirm-wipe: {fds[fd]} answered {answer!r} -- "
                         f"{'confirmed' if ok else 'NOT confirmed'}")
                return ok
        log.error("confirm-wipe: every console closed without an answer -- refusing to wipe")
        return False
    finally:
        for fd in fds:
            os.close(fd)
        subprocess.run(["systemctl", "start", *units], check=False)
