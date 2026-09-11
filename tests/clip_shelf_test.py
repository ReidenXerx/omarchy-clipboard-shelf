#!/usr/bin/python3
"""python3 tests/clip_shelf_test.py -- exercises bin/clip-shelf in a sandbox under
$XDG_RUNTIME_DIR. wl-copy / wl-paste are replaced with recorders, so the real clipboard,
history and pins are never touched."""
import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
BIN = ROOT / "bin"
sys.path.insert(0, str(BIN))
import plugin_safety as safe  # noqa: E402

_loader = importlib.machinery.SourceFileLoader("clip_shelf", str(BIN / "clip-shelf"))
_spec = importlib.util.spec_from_loader("clip_shelf", _loader)
cs = importlib.util.module_from_spec(_spec)
_loader.exec_module(cs)


def png(w=10, h=10, extra=64):
    return (b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + w.to_bytes(4, "big")
            + h.to_bytes(4, "big") + b"\x08\x06\x00\x00\x00" + b"\0" * 4 + b"x" * extra)


def gif(w, h):
    return b"GIF89a" + w.to_bytes(2, "little") + h.to_bytes(2, "little") + b"\0" * 16


def bmp(w, h):
    return (b"BM" + b"\0" * 12 + (40).to_bytes(4, "little") + w.to_bytes(4, "little", signed=True)
            + (-h).to_bytes(4, "little", signed=True) + b"\0" * 16)


def webp(chunk, w, h):
    head = b"RIFF" + b"\0" * 4 + b"WEBP" + chunk + b"\0" * 4
    if chunk == b"VP8 ":
        body = b"\0" * 3 + b"\x9d\x01\x2a" + w.to_bytes(2, "little") + h.to_bytes(2, "little")
    elif chunk == b"VP8L":
        body = b"\x2f" + ((w - 1) | ((h - 1) << 14)).to_bytes(4, "little")
    else:
        body = b"\0" * 4 + (w - 1).to_bytes(3, "little") + (h - 1).to_bytes(3, "little")
    return head + body + b"\0" * 16


def jpeg(w, h):
    return (b"\xff\xd8" + b"\xff\xe0" + (16).to_bytes(2, "big") + b"JFIF\0" + b"\0" * 9
            + b"\xff\xc0" + (17).to_bytes(2, "big") + b"\x08" + h.to_bytes(2, "big")
            + w.to_bytes(2, "big") + b"\x03" + b"\0" * 9 + b"\xff\xd9")


class FakeProc:
    def __init__(self, rc=0):
        self.returncode = rc

    def poll(self):
        return self.returncode


class Sandbox(unittest.TestCase):
    PATCHED = ("HISTORY", "IMAGE_DIR", "PINS", "PREVIEW_DIR", "HISTORY_MAX_BYTES", "IMAGE_MAX_BYTES")

    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="clip-shelf-test-", dir=safe.runtime_dir()))
        os.chmod(self.root, 0o700)
        self.saved = {k: getattr(cs, k) for k in self.PATCHED}
        self.saved_safe = (safe.spawn, safe.run, safe.read_stdin)
        cs.HISTORY = self.root / "state/omarchy/clipboard-history.json"
        cs.IMAGE_DIR = self.root / "state/omarchy/clipboard-images"
        cs.PINS = self.root / "config/omarchy/clipboard-shelf.json"
        cs.PREVIEW_DIR = self.root / "runtime/reidenxerx.clipboard-shelf"
        cs.IMAGE_DIR.mkdir(parents=True)
        os.chmod(cs.IMAGE_DIR, 0o755)
        cs.PINS.parent.mkdir(parents=True)
        self.spawned, self.ran = [], []
        self.spawn_rc = 0
        self.paste = {}

        def fake_spawn(argv, *, timeout=30, input=None, env=None):
            self.spawned.append({"argv": list(argv), "timeout": timeout, "input": input})
            return FakeProc(self.spawn_rc)

        def fake_run(argv, *, input=None, timeout=10.0, max_output=1 << 20, env=None, cwd=None):
            self.ran.append({"argv": list(argv), "timeout": timeout, "max_output": max_output})
            out, truncated = self.paste.get(argv[1], (b"", False))
            return safe.Result(0, out, b"", False, truncated)

        safe.spawn, safe.run = fake_spawn, fake_run

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(cs, k, v)
        safe.spawn, safe.run, safe.read_stdin = self.saved_safe
        shutil.rmtree(self.root, ignore_errors=True)

    # helpers
    def call(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cs.run(["clip-shelf", *args])
        return rc, out.getvalue(), err.getvalue()

    def snapshot(self):
        rc, out, err = self.call("snapshot")
        self.assertEqual(rc, 0, err)
        return json.loads(out)

    def write_history(self, entries):
        cs.HISTORY.write_text(json.dumps(entries))

    def write_pins(self, pins):
        cs.PINS.write_text(json.dumps({"pins": pins}))

    def read_pins(self):
        return json.loads(cs.PINS.read_text())["pins"]

    def image(self, name, blob):
        path = cs.IMAGE_DIR / name
        path.write_bytes(blob)
        return {"type": "image", "mime": "image/png", "path": str(path)}

    @staticmethod
    def fp(entry):
        key = ("image:" + entry["path"]) if entry.get("type") == "image" else ("text:" + entry["text"])
        return hashlib.sha256(key.encode()).hexdigest()[:16]


class Snapshot(Sandbox):
    def test_bounded_and_truncated(self):
        long_text = "y" * 10000
        history = [{"type": "text", "text": f"entry {i}"} for i in range(300)]
        history[0] = {"type": "text", "text": long_text}
        self.write_history(history)
        self.write_pins([{"type": "text", "text": f"pin {i}"} for i in range(150)])
        doc = self.snapshot()
        self.assertEqual(len(doc["history"]), cs.HISTORY_WINDOW)
        self.assertEqual(len(doc["pins"]), cs.MAX_PINS)
        first = doc["history"][0]
        self.assertEqual(len(first["text"]), cs.DISPLAY_CHARS)
        self.assertTrue(first["truncated"])
        self.assertEqual(first["fp"], self.fp({"text": long_text}))
        self.assertEqual(doc["history"][5], {"index": 5, "fp": self.fp({"text": "entry 5"}),
                                             "type": "text", "text": "entry 5", "truncated": False})

    def test_same_snippet_shares_fingerprint(self):
        self.write_history([{"type": "text", "text": "ssh alien-win"}])
        self.write_pins(["ssh alien-win"])
        doc = self.snapshot()
        self.assertEqual(doc["history"][0]["fp"], doc["pins"][0]["fp"])

    def test_images_expose_no_path(self):
        good = self.image("abc.png", png())
        outside = {"type": "image", "mime": "image/png", "path": str(self.root / "elsewhere.png")}
        tiff = {"type": "image", "mime": "image/tiff", "path": str(cs.IMAGE_DIR / "x.tiff")}
        self.write_history([good, outside, tiff, {"type": "text"}, 42, None])
        doc = self.snapshot()
        self.assertEqual(len(doc["history"]), 3)
        for item in doc["history"]:
            self.assertNotIn("path", item)
        self.assertEqual([i["previewable"] for i in doc["history"]], [True, False, False])
        self.assertEqual(doc["history"][0]["name"], "abc.png")
        self.assertEqual(doc["history"][0]["mime"], "image/png")

    def test_output_is_ascii(self):
        self.write_history([{"type": "text", "text": "привіт \ud800 ✓"}])
        rc, out, _ = self.call("snapshot")
        self.assertEqual(rc, 0)
        out.encode("ascii")
        self.assertEqual(json.loads(out)["history"][0]["text"], "привіт \ud800 ✓")

    def test_oversized_history_is_refused_not_parsed(self):
        cs.HISTORY_MAX_BYTES = 100
        self.write_history([{"type": "text", "text": "z" * 200}])
        doc = self.snapshot()
        self.assertEqual(doc["history"], [])
        self.assertTrue(any(e.startswith("history:") for e in doc["errors"]))

    def test_symlinked_history_is_refused(self):
        real = self.root / "real.json"
        real.write_text(json.dumps([{"type": "text", "text": "x"}]))
        cs.HISTORY.parent.mkdir(parents=True, exist_ok=True)
        cs.HISTORY.symlink_to(real)
        doc = self.snapshot()
        self.assertEqual(doc["history"], [])
        self.assertTrue(doc["errors"])

    def test_deep_pins_json_is_refused(self):
        cs.PINS.write_text('{"pins": ' + "[" * 50 + "]" * 50 + "}")
        doc = self.snapshot()
        self.assertEqual(doc["pins"], [])
        self.assertTrue(any(e.startswith("pins:") for e in doc["errors"]))

    def test_missing_files(self):
        self.assertEqual(self.snapshot(), {"history": [], "pins": [], "errors": []})


class Copy(Sandbox):
    def test_text_goes_through_stdin_only(self):
        secret = "hunter2 correct horse"
        entry = {"type": "text", "text": secret}
        self.write_history([{"type": "text", "text": "other"}, entry])
        rc, _, err = self.call("copy", "--history", "1", "--fingerprint", self.fp(entry))
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(self.spawned), 1)
        call = self.spawned[0]
        self.assertEqual(call["argv"], ["wl-copy"])
        self.assertEqual(call["input"], secret.encode())
        self.assertEqual(call["timeout"], cs.COPY_TIMEOUT)
        self.assertNotIn(secret, " ".join(call["argv"]))

    def test_follows_entry_when_history_shifts(self):
        entry = {"type": "text", "text": "the one"}
        self.write_history([{"type": "text", "text": "new copy"}, {"type": "text", "text": "x"}, entry])
        rc, _, err = self.call("copy", "--history", "0", "--fingerprint", self.fp(entry))
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.spawned[0]["input"], b"the one")

    def test_wrong_fingerprint_copies_nothing(self):
        self.write_history([{"type": "text", "text": "a"}])
        rc, _, err = self.call("copy", "--history", "0", "--fingerprint", "0" * 16)
        self.assertEqual(rc, 1)
        self.assertIn("no longer", err)
        self.assertEqual(self.spawned, [])

    def test_rejects_malformed_references(self):
        self.write_history([{"type": "text", "text": "a"}])
        for args in (["--history", "-1", "--fingerprint", "0" * 16],
                     ["--history", "0", "--fingerprint", "XYZ"],
                     ["--history", "0"],
                     ["--history", "99999", "--fingerprint", "0" * 16]):
            rc, _, _ = self.call("copy", *args)
            self.assertNotEqual(rc, 0, args)
        self.assertEqual(self.spawned, [])

    def test_pin_by_reference(self):
        entry = {"type": "text", "text": "pinned secret"}
        self.write_pins([entry, "second"])
        rc, _, err = self.call("copy", "--pin", "0", "--fingerprint", self.fp(entry))
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.spawned[0]["input"], b"pinned secret")

    def test_image_with_type(self):
        blob = png()
        entry = self.image("a.png", blob)
        self.write_history([entry])
        rc, _, err = self.call("copy", "--history", "0", "--fingerprint", self.fp(entry))
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.spawned[0]["argv"], ["wl-copy", "--type", "image/png"])
        self.assertEqual(self.spawned[0]["input"], blob)

    def assert_image_refused(self, entry):
        self.write_history([entry])
        rc, _, err = self.call("copy", "--history", "0", "--fingerprint", self.fp(entry))
        self.assertEqual(rc, 1, err)
        self.assertEqual(self.spawned, [])
        return err

    def test_image_outside_directory(self):
        outside = self.root / "outside.png"
        outside.write_bytes(png())
        self.assert_image_refused({"type": "image", "path": str(outside)})
        self.assert_image_refused({"type": "image", "path": str(cs.IMAGE_DIR / "sub" / ".." / "x.png")})

    def test_image_symlink(self):
        outside = self.root / "outside.png"
        outside.write_bytes(png())
        (cs.IMAGE_DIR / "link.png").symlink_to(outside)
        self.assertIn("symlink", self.assert_image_refused({"type": "image", "path": str(cs.IMAGE_DIR / "link.png")}))

    def test_image_type_mismatch_and_unsupported(self):
        self.assert_image_refused(self.image("fake.png", gif(4, 4)))
        self.assert_image_refused(self.image("x.tiff", b"II*\0" + b"\0" * 40))
        self.assert_image_refused(self.image("x.svg", b"<svg/>"))

    def test_image_too_large(self):
        cs.IMAGE_MAX_BYTES = 50
        self.assertIn("larger", self.assert_image_refused(self.image("big.png", png(extra=100))))

    def test_wl_copy_failure_reported(self):
        self.spawn_rc = 1
        entry = {"type": "text", "text": "a"}
        self.write_history([entry])
        rc, _, err = self.call("copy", "--history", "0", "--fingerprint", self.fp(entry))
        self.assertEqual(rc, 1)
        self.assertIn("wl-copy", err)


class Pins(Sandbox):
    def test_pin_and_unpin_by_reference(self):
        entry = {"type": "text", "text": "keep me"}
        self.write_history([entry])
        self.write_pins([{"type": "text", "text": "older", "label": "L"}])
        rc, _, err = self.call("pin", "--history", "0", "--fingerprint", self.fp(entry))
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.read_pins(), [{"type": "text", "text": "keep me"},
                                            {"type": "text", "text": "older", "label": "L"}])
        self.assertEqual(os.stat(cs.PINS).st_mode & 0o022, 0)
        rc, _, err = self.call("unpin", "--pin", "0", "--fingerprint", self.fp(entry))
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.read_pins(), [{"type": "text", "text": "older", "label": "L"}])
        self.assertEqual([p for p in os.listdir(cs.PINS.parent) if p.endswith(".tmp")], [])

    def test_unpin_follows_moved_pin(self):
        target = {"type": "text", "text": "b"}
        self.write_pins(["a", "b", "c"])
        rc, _, err = self.call("unpin", "--pin", "0", "--fingerprint", self.fp(target))
        self.assertEqual(rc, 0, err)
        self.assertEqual([p["text"] for p in self.read_pins()], ["a", "c"])

    def test_pin_image_records_canonical_mime(self):
        entry = self.image("shot.jpg", jpeg(3, 3))
        entry["mime"] = "text/html"
        self.write_history([entry])
        rc, _, err = self.call("pin", "--history", "0", "--fingerprint", self.fp(entry))
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.read_pins(), [{"type": "image", "path": entry["path"], "mime": "image/jpeg"}])

    def test_malformed_pins_file_is_never_overwritten(self):
        cs.PINS.write_text("{not json")
        entry = {"type": "text", "text": "x"}
        self.write_history([entry])
        rc, _, err = self.call("pin", "--history", "0", "--fingerprint", self.fp(entry))
        self.assertEqual(rc, 1)
        self.assertEqual(cs.PINS.read_text(), "{not json")

    def test_symlinked_pins_file_is_never_followed(self):
        victim = self.root / "victim.json"
        victim.write_text("keep")
        cs.PINS.symlink_to(victim)
        rc, _, _ = self.call("pin", "text")
        self.assertEqual(rc, 1)
        self.assertEqual(victim.read_text(), "keep")

    def test_pin_count_and_size_caps(self):
        self.write_pins([f"p{i}" for i in range(cs.MAX_PINS)])
        rc, _, err = self.call("pin", "one more")
        self.assertEqual(rc, 1)
        self.assertIn("at most", err)
        self.write_pins([])
        rc, _, err = self.call("pin", "z" * (cs.PINS_MAX_BYTES + 10))
        self.assertEqual(rc, 1)
        self.assertIn("exceed", err)
        self.assertEqual(self.read_pins(), [])

    def test_cli_flow(self):
        self.assertEqual(self.call("pin", "ssh", "alien-win")[0], 0)
        self.assertEqual(self.call("pin", "second")[0], 0)
        rc, out, _ = self.call("list")
        self.assertEqual(rc, 0)
        self.assertEqual(out.splitlines(), ["  1  second", "  2  ssh alien-win"])
        self.assertEqual(self.call("copy", "2")[0], 0)
        self.assertEqual(self.spawned[-1]["input"], b"ssh alien-win")
        self.assertEqual(self.call("unpin", "1")[0], 0)
        self.assertEqual(self.call("unpin", "5")[0], 1)
        self.assertEqual([p["text"] for p in self.read_pins()], ["ssh alien-win"])
        self.assertEqual(self.call("clear")[0], 0)
        self.assertEqual(self.read_pins(), [])

    def test_pin_stdin(self):
        safe.read_stdin = lambda max_bytes: b"from stdin\n"
        rc, _, err = self.call("pin", "--stdin")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.read_pins(), [{"type": "text", "text": "from stdin"}])

        def too_big(max_bytes):
            raise safe.TooLarge("stdin exceeds limit")
        safe.read_stdin = too_big
        self.assertEqual(self.call("pin", "--stdin")[0], 1)

    def test_pin_clipboard_bounded(self):
        self.paste = {"--list-types": (b"text/plain\nUTF8_STRING\n", False),
                      "--no-newline": ("clip ✓".encode(), False)}
        rc, _, err = self.call("pin", "--clipboard")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.read_pins(), [{"type": "text", "text": "clip ✓"}])
        text_call = self.ran[-1]
        self.assertEqual(text_call["argv"], ["wl-paste", "--no-newline", "--type", "text"])
        self.assertEqual(text_call["timeout"], 5)
        self.assertEqual(text_call["max_output"], 1024 * 1024)

    def test_pin_clipboard_refuses_password_and_oversize(self):
        self.paste = {"--list-types": (b"text/plain\nx-kde-passwordManagerHint\n", False),
                      "--no-newline": (b"secret", False)}
        rc, _, err = self.call("pin", "--clipboard")
        self.assertEqual(rc, 1)
        self.assertIn("password", err)
        self.paste = {"--list-types": (b"text/plain\n", False), "--no-newline": (b"x" * 10, True)}
        rc, _, err = self.call("pin", "--clipboard")
        self.assertEqual(rc, 1)
        self.assertIn("larger", err)
        self.assertFalse(cs.PINS.exists())


class Preview(Sandbox):
    def preview(self, entry, index=0):
        return self.call("preview", "--history", str(index), "--fingerprint", self.fp(entry))

    def test_verified_private_copy(self):
        blob = png(640, 480)
        entry = self.image("shot.png", blob)
        self.write_history([entry])
        rc, out, err = self.preview(entry)
        self.assertEqual(rc, 0, err)
        doc = json.loads(out)
        self.assertEqual((doc["width"], doc["height"], doc["fp"]), (640, 480, self.fp(entry)))
        copy = cs.PREVIEW_DIR / f"preview-{self.fp(entry)}.png"
        self.assertEqual(doc["url"], "file://" + str(copy))
        self.assertEqual(copy.read_bytes(), blob)
        self.assertEqual(os.stat(copy).st_mode & 0o777, 0o600)

    def test_dimension_cap(self):
        entry = self.image("wide.png", png(cs.PREVIEW_MAX_SIDE + 1, 10))
        self.write_history([entry])
        rc, _, err = self.preview(entry)
        self.assertEqual(rc, 1)
        self.assertIn("too large", err)
        entry = self.image("huge.png", png(10000, 10000))
        self.write_history([entry])
        self.assertEqual(self.preview(entry)[0], 1)
        self.assertFalse(any(cs.PREVIEW_DIR.glob("preview-*")) if cs.PREVIEW_DIR.exists() else False)

    def test_text_entry_is_not_previewed(self):
        entry = {"type": "text", "text": "/etc/passwd"}
        self.write_history([entry])
        self.assertEqual(self.preview(entry)[0], 1)

    def test_previews_are_pruned(self):
        entries = [self.image(f"i{i}.png", png(5, 5)) for i in range(7)]
        self.write_history(entries)
        for i, entry in enumerate(entries):
            self.assertEqual(self.preview(entry, i)[0], 0)
        left = sorted(p.name for p in cs.PREVIEW_DIR.iterdir())
        self.assertEqual(len(left), cs.PREVIEW_KEEP)
        self.assertIn(f"preview-{self.fp(entries[-1])}.png", left)


class Sniff(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(cs.sniff_image(png(7, 9)), ("image/png", 7, 9))
        self.assertEqual(cs.sniff_image(gif(7, 9)), ("image/gif", 7, 9))
        self.assertEqual(cs.sniff_image(bmp(7, 9)), ("image/bmp", 7, 9))
        self.assertEqual(cs.sniff_image(jpeg(7, 9)), ("image/jpeg", 7, 9))
        for chunk in (b"VP8 ", b"VP8L", b"VP8X"):
            self.assertEqual(cs.sniff_image(webp(chunk, 7, 9)), ("image/webp", 7, 9), chunk)

    def test_garbage(self):
        for blob in (b"", b"\x89PNG", b"\xff\xd8" + b"\xff\x00" * 10, b"\xff\xd8" + b"\xff" * 100000,
                     png(0, 5), b"<svg/>", b"RIFF\0\0\0\0WEBPVP8Z" + b"\0" * 20):
            with self.assertRaises(cs.ShelfError):
                cs.sniff_image(blob)


class Static(unittest.TestCase):
    """What the reviewer greps for."""

    def test_helper(self):
        src = (BIN / "clip-shelf").read_text()
        self.assertTrue(src.startswith("#!/usr/bin/python3\n"))
        for bad in ("subprocess", "shell=True", "os.system", "os.popen", "open(", "read_text", "write_text", "read_bytes"):
            self.assertNotIn(bad, src, bad)

    def test_installer(self):
        src = (BIN / "clip-shelf-menu-install").read_text()
        self.assertTrue(src.startswith("#!/usr/bin/python3\n"))
        self.assertNotIn(".tmp", src.replace("temporary", ""))
        self.assertNotIn(".bak", src)

    def test_qml(self):
        for name in ("Panel.qml", "BarWidget.qml"):
            # Code only: comments may explain what the service upstream runs.
            code = re.sub(r"(^|\s)//[^\n]*", r"\1", (ROOT / name).read_text())
            for bad in ('"sh"', "bash", "FileView", "wl-copy", "wl-paste", "entry.path", "entry.text"):
                self.assertFalse(bad in code, f"{name} contains {bad}")
        panel = (ROOT / "Panel.qml").read_text()
        self.assertIn('["/usr/bin/python3", pluginBin + "clip-shelf"]', panel)
        self.assertEqual(len(re.findall(r"\bProcess \{", panel)), len(re.findall(r"\bWatchdog \{ id:", panel)))
        self.assertIn("sourceSize.width", panel)
        self.assertIn("sourceSize.height", panel)

    def test_menu_uses_absolute_interpreter(self):
        src = (ROOT / "menu.jsonc").read_text()
        for action in re.findall(r'"action": "([^"]*)"', src):
            if "@PLUGIN_BIN@" in action:
                self.assertIn("/usr/bin/python3 @PLUGIN_BIN@/", action)


if __name__ == "__main__":
    unittest.main(verbosity=1)
