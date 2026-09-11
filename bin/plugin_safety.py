"""plugin_safety -- hardened file, process and network helpers for Omarchy plugins.

Vendored verbatim into each plugin as bin/plugin_safety.py. Keep every copy identical to
the master in ~/Projects/omarchy-plugin-safety and bump VERSION when it changes.

Threat model: another process running as the same user may pre-position symlinks, FIFOs,
hard-to-read or oversized files in the user's config/state/runtime directories, or shadow
executables through PATH. Everything here fails closed:

  * tools resolve only to root-owned, non-writable files in /usr/bin; children get
    PATH=/usr/bin, a hard deadline, an output ceiling and a whole-process-group kill;
  * files are reached by walking from the home or runtime directory one component at a
    time with O_NOFOLLOW, checking each directory is ours and not group/other-writable;
  * reads are capped and refuse non-regular or foreign-owned files;
  * writes go to a random O_EXCL temporary file in the same directory, are fsynced, and
    atomically renamed over a destination that is a regular file we own (or absent);
  * JSON is bounded in depth, item count and string length;
  * HTTP is HTTPS-only, never redirected to anything else, and size-capped.
"""
import ctypes
import errno
import json
import os
import pwd
import re
import secrets
import signal
import stat
import subprocess
import threading
import urllib.error
import urllib.request

VERSION = "1"

TRUSTED_TOOL_DIR = "/usr/bin"
SAFE_PATH = "/usr/bin"
_TOOL_NAME = re.compile(r"[A-Za-z0-9._+-]{1,64}")


class UnsafeError(Exception):
    """A path, file, tool or input failed a safety check."""


class TooLarge(UnsafeError):
    """Input exceeded its size or structure limit."""


# ------------------------------------------------------------------ tools

_tool_cache = {}


def _trusted_system_file(path):
    """Root-owned regular executable, not group/other-writable, in root-owned,
    non-writable directories all the way up."""
    real = os.path.realpath(path)
    if not real.startswith("/usr/"):
        raise UnsafeError(f"{path}: resolves outside /usr ({real})")
    st = os.stat(real)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022 or not st.st_mode & 0o111:
        raise UnsafeError(f"{path}: not a root-owned, non-writable executable")
    directory = os.path.dirname(real)
    while True:
        dst = os.stat(directory)
        if dst.st_uid != 0 or dst.st_mode & 0o022:
            raise UnsafeError(f"{directory}: not a root-owned, non-writable directory")
        if directory == "/":
            break
        directory = os.path.dirname(directory)
    return real


def tool(name):
    """Absolute path of a trusted system tool, or UnsafeError. Never consults PATH."""
    if name in _tool_cache:
        return _tool_cache[name]
    if not _TOOL_NAME.fullmatch(name or ""):
        raise UnsafeError(f"invalid tool name: {name!r}")
    path = f"{TRUSTED_TOOL_DIR}/{name}"
    if not os.path.lexists(path):
        raise UnsafeError(f"{name} is not installed in {TRUSTED_TOOL_DIR}")
    _trusted_system_file(path)
    _tool_cache[name] = path
    return path


def has_tool(name):
    try:
        tool(name)
        return True
    except UnsafeError:
        return False


class Result:
    __slots__ = ("returncode", "stdout", "stderr", "timed_out", "truncated")

    def __init__(self, returncode, stdout, stderr, timed_out, truncated):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr
        self.timed_out, self.truncated = timed_out, truncated

    @property
    def ok(self):
        return self.returncode == 0 and not self.timed_out and not self.truncated

    def text(self):
        return self.stdout.decode("utf-8", "replace")


def _child_env(extra=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LD_", "PYTHON"))}
    env["PATH"] = SAFE_PATH
    if extra:
        env.update(extra)
    return env


def _kill_group(proc, grace=0.5):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue


def _resolve_exe(exe):
    if exe.startswith("/"):
        return exe
    return tool(exe)


def run(argv, *, input=None, timeout=10.0, max_output=1 << 20, env=None, cwd=None):
    """Run argv with a deadline and an output ceiling; the whole process group is killed
    on overrun. argv[0] is a tool name (resolved with tool()) or an absolute path the
    caller has already validated. Input goes through stdin, never argv."""
    exe = _resolve_exe(argv[0])
    proc = subprocess.Popen(
        [exe, *[str(a) for a in argv[1:]]],
        stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=_child_env(env), cwd=cwd, start_new_session=True, close_fds=True)
    state = {"truncated": False}
    chunks = {"out": [], "err": []}

    def pump(stream, key):
        total = 0
        try:
            while True:
                chunk = stream.read1(65536)
                if not chunk:
                    break
                if total < max_output:
                    chunks[key].append(chunk[:max_output - total])
                total += len(chunk)
                if total > max_output:
                    state["truncated"] = True
                    _kill_group(proc)
                    break
        except (OSError, ValueError):
            pass

    def feed():
        try:
            proc.stdin.write(input if isinstance(input, (bytes, bytearray)) else str(input).encode("utf-8"))
        except (BrokenPipeError, OSError, ValueError):
            pass
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass

    threads = [threading.Thread(target=pump, args=(proc.stdout, "out"), daemon=True),
               threading.Thread(target=pump, args=(proc.stderr, "err"), daemon=True)]
    if input is not None:
        threads.append(threading.Thread(target=feed, daemon=True))
    for t in threads:
        t.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_group(proc)
    for t in threads:
        t.join(timeout=2)
    for stream in (proc.stdout, proc.stderr):
        try:
            stream.close()
        except OSError:
            pass
    return Result(proc.returncode if proc.returncode is not None else -9,
                  b"".join(chunks["out"]), b"".join(chunks["err"]), timed_out, state["truncated"])


def spawn(argv, *, timeout=30, input=None, env=None):
    """Fire-and-forget, still bounded: the child runs under /usr/bin/timeout in its own
    session with stdio detached, so it can neither hang forever nor outlive its deadline."""
    exe = _resolve_exe(argv[0])
    proc = subprocess.Popen(
        [tool("timeout"), "--kill-after=2", str(int(timeout)), exe, *[str(a) for a in argv[1:]]],
        stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=_child_env(env), start_new_session=True, close_fds=True)
    if input is not None:
        try:
            proc.stdin.write(input if isinstance(input, (bytes, bytearray)) else str(input).encode("utf-8"))
        except (BrokenPipeError, OSError):
            pass
        finally:
            proc.stdin.close()
    return proc


def die_with_parent(sig=signal.SIGTERM):
    """For long-lived helpers started by the shell: exit when the parent does."""
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(1, int(sig), 0, 0, 0)  # PR_SET_PDEATHSIG
    except OSError:
        pass
    if os.getppid() == 1:
        os._exit(0)


# ------------------------------------------------------------------ paths

def home_dir():
    return os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)


def runtime_dir():
    path = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return os.path.realpath(path)


def _anchors():
    return [runtime_dir(), home_dir()]


def _split(path):
    p = os.path.abspath(os.fspath(path))
    if "\0" in p:
        raise UnsafeError("path contains NUL")
    for anchor in _anchors():
        if p == anchor or p.startswith(anchor + "/"):
            parts = [c for c in p[len(anchor):].split("/") if c]
            if not parts:
                raise UnsafeError(f"{p}: is an anchor directory, not a file")
            return anchor, parts
    raise UnsafeError(f"{p}: outside the home and runtime directories")


def _check_dir(fd, where):
    st = os.fstat(fd)
    if st.st_uid != os.getuid() or st.st_mode & 0o022:
        raise UnsafeError(f"{where}: directory is not ours or is group/other-writable")


def _open_dir(anchor, parts, create=False, mode=0o700):
    fd = os.open(anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        _check_dir(fd, anchor)
        where = anchor
        for part in parts:
            where = where + "/" + part
            try:
                nfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, mode, dir_fd=fd)
                except FileExistsError:
                    pass
                nfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            except OSError as e:
                if e.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise UnsafeError(f"{where}: symlink or not a directory") from None
                raise
            os.close(fd)
            fd = nfd
            _check_dir(fd, where)
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_file(path, max_bytes):
    """Bytes of a regular file we own, at most max_bytes; None if it does not exist."""
    anchor, parts = _split(path)
    try:
        dfd = _open_dir(anchor, parts[:-1])
    except FileNotFoundError:
        return None
    try:
        try:
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dfd)
        except FileNotFoundError:
            return None
        except OSError as e:
            if e.errno == errno.ELOOP:
                raise UnsafeError(f"{path}: is a symlink") from None
            raise
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
                raise UnsafeError(f"{path}: not a regular file we own")
            if st.st_size > max_bytes:
                raise TooLarge(f"{path}: {st.st_size} bytes exceeds {max_bytes}")
            data, want = [], max_bytes + 1
            while want > 0:
                chunk = os.read(fd, min(65536, want))
                if not chunk:
                    break
                data.append(chunk)
                want -= len(chunk)
            blob = b"".join(data)
            if len(blob) > max_bytes:
                raise TooLarge(f"{path}: exceeds {max_bytes} bytes")
            return blob
        finally:
            os.close(fd)
    finally:
        os.close(dfd)


def read_system_file(path, max_bytes):
    """Small reads from /proc, /sys or /usr/share (kernel and package data), capped,
    never following a final symlink."""
    p = os.path.abspath(path)
    if not p.startswith(("/proc/", "/sys/", "/usr/share/")):
        raise UnsafeError(f"{p}: not a system data path")
    try:
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    try:
        data = os.read(fd, max_bytes + 1)
        if len(data) > max_bytes:
            raise TooLarge(f"{p}: exceeds {max_bytes} bytes")
        return data
    finally:
        os.close(fd)


def write_file(path, data, mode=0o600):
    """Atomically replace path with data. Parents are created 0700 when missing."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    anchor, parts = _split(path)
    name = parts[-1]
    dfd = _open_dir(anchor, parts[:-1], create=True)
    tmp = None
    try:
        try:
            st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
        except FileNotFoundError:
            st = None
        if st is not None:
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
                raise UnsafeError(f"{path}: existing destination is not a regular file we own")
            mode = stat.S_IMODE(st.st_mode) & ~0o022
        tmp = f".{name}.{secrets.token_hex(8)}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode, dir_fd=dfd)
        try:
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fchmod(fd, mode)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.rename(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
        tmp = None
        os.fsync(dfd)
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except OSError:
                pass
        os.close(dfd)


def remove_file(path):
    """Unlink a regular file we own. True if removed, False if absent."""
    anchor, parts = _split(path)
    try:
        dfd = _open_dir(anchor, parts[:-1])
    except FileNotFoundError:
        return False
    try:
        try:
            st = os.stat(parts[-1], dir_fd=dfd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
            raise UnsafeError(f"{path}: not a regular file we own")
        os.unlink(parts[-1], dir_fd=dfd)
        return True
    finally:
        os.close(dfd)


def list_dir(path, max_entries=1000):
    """[(name, lstat)] for a directory we own, capped; [] if it does not exist."""
    anchor, parts = _split(path)
    try:
        dfd = _open_dir(anchor, parts)
    except FileNotFoundError:
        return []
    try:
        out = []
        with os.scandir(dfd) as it:
            for entry in it:
                if len(out) >= max_entries:
                    break
                out.append((entry.name, entry.stat(follow_symlinks=False)))
        return sorted(out)
    finally:
        os.close(dfd)


def ensure_dir(path, mode=0o700):
    """Create a directory (and missing parents) we own, without following symlinks."""
    anchor, parts = _split_dir(path)
    os.close(_open_dir(anchor, parts, create=True, mode=mode))


def _split_dir(path):
    p = os.path.abspath(os.fspath(path))
    for anchor in _anchors():
        if p == anchor:
            return anchor, []
        if p.startswith(anchor + "/"):
            return anchor, [c for c in p[len(anchor):].split("/") if c]
    raise UnsafeError(f"{p}: outside the home and runtime directories")


def trusted_user_file(path):
    """For executables the user supplies (e.g. provider scripts): a regular file we own,
    not group/other-writable, reached without symlinks. Returns the absolute path."""
    anchor, parts = _split(path)
    dfd = _open_dir(anchor, parts[:-1])
    try:
        st = os.stat(parts[-1], dir_fd=dfd, follow_symlinks=False)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o022:
            raise UnsafeError(f"{path}: not a regular, non-writable file we own")
        return os.path.abspath(path)
    finally:
        os.close(dfd)


# ------------------------------------------------------------------ JSON

def check_json(value, *, max_depth=32, max_items=20000, max_string=1 << 20):
    """Raise TooLarge if a decoded JSON value exceeds depth/item/string limits."""
    stack = [(value, 1)]
    items = 0
    while stack:
        node, depth = stack.pop()
        if depth > max_depth:
            raise TooLarge("JSON nested too deeply")
        if isinstance(node, dict):
            items += len(node)
            for k, v in node.items():
                if len(k) > max_string:
                    raise TooLarge("JSON key too long")
                stack.append((v, depth + 1))
        elif isinstance(node, list):
            items += len(node)
            stack.extend((v, depth + 1) for v in node)
        elif isinstance(node, str) and len(node) > max_string:
            raise TooLarge("JSON string too long")
        if items > max_items:
            raise TooLarge("JSON has too many items")
    return value


def loads(data, **limits):
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8")
    return check_json(json.loads(data), **limits)


def read_json(path, max_bytes, default=None, **limits):
    """Parsed JSON from a safe, capped read; default when missing or malformed.
    UnsafeError still propagates: a symlink or foreign file is not "missing"."""
    blob = read_file(path, max_bytes)
    if blob is None:
        return default
    try:
        return loads(blob, **limits)
    except (ValueError, UnicodeDecodeError):
        return default


def write_json(path, value, mode=0o600, **dump):
    write_file(path, (json.dumps(value, ensure_ascii=False, **dump) + "\n").encode("utf-8"), mode)


def read_stdin(max_bytes):
    import sys
    data = sys.stdin.buffer.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise TooLarge("stdin exceeds limit")
    return data


# ------------------------------------------------------------------ HTTP

class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise urllib.error.HTTPError(newurl, code, "refusing non-HTTPS redirect", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_HttpsOnlyRedirects)


def http_request(url, *, method="GET", body=None, headers=None, timeout=12, max_bytes=1 << 20):
    """(status, body bytes) for an HTTPS request; both success and error bodies are capped.
    Raises TooLarge past max_bytes and OSError/URLError for transport failures."""
    if not str(url).lower().startswith("https://"):
        raise UnsafeError("only HTTPS URLs are allowed")
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})

    def capped(stream):
        blob = stream.read(max_bytes + 1)
        if len(blob) > max_bytes:
            raise TooLarge("HTTP response exceeds limit")
        return blob

    try:
        with _opener.open(req, timeout=timeout) as resp:
            return resp.status, capped(resp)
    except urllib.error.HTTPError as e:
        try:
            return e.code, capped(e)
        finally:
            e.close()
