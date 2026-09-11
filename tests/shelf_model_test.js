// node tests/shelf_model_test.js -- the panel's pure model, loaded outside QML.
"use strict"
const assert = require("assert")
const fs = require("fs")
const path = require("path")
const vm = require("vm")

const src = fs.readFileSync(path.join(__dirname, "..", "ShelfModel.js"), "utf8")
  .replace(/^\.pragma library\s*$/m, "")
const Shelf = {}
vm.createContext(Shelf)
vm.runInContext(src, Shelf)

// Values cross a vm realm, so compare plain copies.
const plain = (v) => JSON.parse(JSON.stringify(v))
const FP = "0123456789abcdef"
const FP2 = "fedcba9876543210"
let failures = 0

function test(name, fn) {
  try { fn(); console.log("ok   " + name) } catch (e) { failures++; console.log("FAIL " + name + "\n     " + e.message) }
}

test("classify", () => {
  assert.strictEqual(Shelf.classify({ text: "https://omarchy.org" }), "url")
  assert.strictEqual(Shelf.classify({ text: " #ff8800 " }), "color")
  assert.strictEqual(Shelf.classify({ text: "a@b.co" }), "email")
  assert.strictEqual(Shelf.classify({ text: "~/.config/hypr" }), "path")
  assert.strictEqual(Shelf.classify({ text: "const a = 1\nfoo()" }), "code")
  assert.strictEqual(Shelf.classify({ type: "image", name: "a.png" }), "image")
  assert.strictEqual(Shelf.classify({ text: "plain words" }), "text")
})

test("collapse", () => {
  assert.strictEqual(Shelf.collapse("\n\n  a   b\n"), "a b")
  assert.strictEqual(Shelf.collapse("abcdef", 4), "abc…")
})

test("snapshotEntries keeps only well-formed references", () => {
  const out = plain(Shelf.snapshotEntries([
    { index: 0, fp: FP, type: "text", text: "hello", truncated: false },
    { index: 1, fp: FP2, type: "image", name: "a.png", mime: "image/png", previewable: true, path: "/etc/passwd" },
    { index: -1, fp: FP, type: "text", text: "neg" },
    { index: 1.5, fp: FP, type: "text", text: "frac" },
    { index: "2", fp: FP, type: "text", text: "string index" },
    { index: 3, fp: "ABC", type: "text", text: "bad fp" },
    { index: 4, fp: FP, type: "text", text: 5 },
    null, "x",
  ], "history", 200))
  assert.deepStrictEqual(out, [
    { source: "history", index: 0, fp: FP, type: "text", text: "hello", truncated: false, label: "" },
    { source: "history", index: 1, fp: FP2, type: "image", name: "a.png", mime: "image/png", previewable: true },
  ])
})

test("snapshotEntries bounds count and text", () => {
  const many = []
  for (let i = 0; i < 500; i++) many.push({ index: i, fp: FP, type: "text", text: "x".repeat(5000) })
  assert.strictEqual(Shelf.snapshotEntries(many, "history", 999).length, 200)
  const pins = Shelf.snapshotEntries(many, "pin", 999)
  assert.strictEqual(pins.length, 100)
  assert.strictEqual(pins[0].text.length, 2048)
  assert.strictEqual(plain(Shelf.snapshotEntries({ not: "a list" }, "pin")).length, 0)
})

test("refArgs never carries content", () => {
  const entry = { source: "pin", index: 3, fp: FP, type: "text", text: "secret" }
  assert.deepStrictEqual(plain(Shelf.refArgs("copy", entry)), ["copy", "--pin", "3", "--fingerprint", FP])
  assert.deepStrictEqual(plain(Shelf.refArgs("pin", { source: "history", index: 0, fp: FP })),
                         ["pin", "--history", "0", "--fingerprint", FP])
  assert.deepStrictEqual(plain(Shelf.refArgs("copy", { source: "pin", index: 1, fp: "nope" })), [])
  assert.deepStrictEqual(plain(Shelf.refArgs("copy", { source: "other", index: 1, fp: FP })), [])
  assert.deepStrictEqual(plain(Shelf.refArgs("copy", { source: "pin", index: -2, fp: FP })), [])
  assert.deepStrictEqual(plain(Shelf.refArgs("copy", null)), [])
})

test("buildRows: pins first, deduped by fingerprint, filtered, capped", () => {
  const pins = Shelf.snapshotEntries([{ index: 0, fp: FP, type: "text", text: "ssh alien-win" }], "pin")
  const history = Shelf.snapshotEntries([
    { index: 0, fp: FP2, type: "text", text: "https://omarchy.org" },
    { index: 1, fp: FP, type: "text", text: "ssh alien-win" },
    { index: 2, fp: "1111111111111111", type: "image", name: "shot.png", previewable: true },
  ], "history")
  const rows = plain(Shelf.buildRows(history, pins, "", 60))
  assert.deepStrictEqual(rows.map(r => [r.pinned, r.kind, r.preview]), [
    [true, "text", "ssh alien-win"], [false, "url", "https://omarchy.org"], [false, "image", "shot.png"],
  ])
  assert.strictEqual(rows[0].entry.source, "pin")
  assert.deepStrictEqual(plain(Shelf.buildRows(history, pins, "shot", 60)).map(r => r.preview), ["shot.png"])
  assert.deepStrictEqual(plain(Shelf.buildRows(history, pins, "url", 60)).map(r => r.kind), ["url"])
  assert.strictEqual(Shelf.buildRows(history, pins, "", 2).length, 2)
})

test("clampIndex wraps", () => {
  assert.strictEqual(Shelf.clampIndex(-1, 5), 4)
  assert.strictEqual(Shelf.clampIndex(5, 5), 0)
  assert.strictEqual(Shelf.clampIndex(2, 0), 0)
})

if (failures) { console.log(failures + " failed"); process.exit(1) }
console.log("all passed")
