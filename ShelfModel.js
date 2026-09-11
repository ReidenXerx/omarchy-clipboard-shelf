.pragma library

// Pure logic for the clipboard shelf. No QML, no I/O, so it can be exercised with node.
//
// Entries come from `clip-shelf snapshot`, which reads the history and pins files with
// size, depth and count limits and sends display text only (truncated). The panel never
// holds a full clipboard entry: it addresses one by { source, index, fp } and the helper
// re-reads it from disk.

// ---------------------------------------------------------------- kinds
//
// A clipboard is mostly text, but "mostly text" is not useful when you are scanning a
// list for the URL you copied two minutes ago. Classifying each entry lets the row carry
// a glyph you can aim at, and lets a colour show its actual colour.

var URL_RE = /^(https?|ftp|ssh|git|magnet):\/\/\S+$/i
var HEX_RE = /^#([0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$/i
var PATH_RE = /^(~|\/|\.\/|\.\.\/)[^\n]*$/
var EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
// Deliberately loose: anything multi-line carrying punctuation that prose rarely uses.
var CODE_HINT_RE = /[{};=<>]|^\s{2,}\S|\bfunction\b|\bconst\b|\bdef\b|\bimport\b/
var FP_RE = /^[0-9a-f]{16}$/

var HISTORY_LIMIT = 200
var PINS_LIMIT = 100
var TEXT_LIMIT = 2048

function classify(entry) {
  if (!entry) return "text"
  if (String(entry.type) === "image") return "image"
  var text = String(entry.text || "")
  var trimmed = text.trim()
  if (trimmed === "") return "text"
  if (URL_RE.test(trimmed)) return "url"
  if (HEX_RE.test(trimmed)) return "color"
  if (EMAIL_RE.test(trimmed)) return "email"
  if (trimmed.indexOf("\n") < 0 && PATH_RE.test(trimmed)) return "path"
  if (trimmed.indexOf("\n") >= 0 && CODE_HINT_RE.test(trimmed)) return "code"
  return "text"
}

function glyphFor(kind) {
  switch (kind) {
    case "url":   return "󰆟"
    case "color": return "󰽜"
    case "email": return "󰋠"
    case "path":  return "󰉋"
    case "code":  return "󰅩"
    case "image": return "󰣗"
    default:      return "󰑿"
  }
}

// Only a colour entry has one; everything else renders with the theme's own colours.
function swatchFor(entry) {
  if (classify(entry) !== "color") return ""
  return String(entry.text || "").trim()
}

// ---------------------------------------------------------------- snapshot

// Re-checks every field of the helper's output, so a malformed entry can neither reach
// the UI with an unexpected shape nor become a command argument.
function snapshotEntries(list, source, limit) {
  var out = []
  if (!Array.isArray(list)) return out
  var max = Math.min(limit === undefined ? HISTORY_LIMIT : limit,
                     source === "pin" ? PINS_LIMIT : HISTORY_LIMIT)
  for (var i = 0; i < list.length && out.length < max; i++) {
    var e = list[i]
    if (!e || typeof e !== "object") continue
    var index = e.index
    if (typeof index !== "number" || !isFinite(index) || index < 0 || index > 9999 ||
        Math.floor(index) !== index) continue
    if (typeof e.fp !== "string" || !FP_RE.test(e.fp)) continue
    if (e.type === "image") {
      out.push({ source: source, index: index, fp: e.fp, type: "image",
                 name: String(e.name || "").slice(0, 128), mime: String(e.mime || "").slice(0, 64),
                 previewable: e.previewable === true })
    } else if (typeof e.text === "string") {
      out.push({ source: source, index: index, fp: e.fp, type: "text",
                 text: e.text.slice(0, TEXT_LIMIT), truncated: e.truncated === true,
                 label: typeof e.label === "string" ? e.label.slice(0, 200) : "" })
    }
  }
  return out
}

// Arguments that name an entry by reference: never its text or path.
function refArgs(verb, entry) {
  if (!entry || typeof entry.fp !== "string" || !FP_RE.test(entry.fp)) return []
  if (typeof entry.index !== "number" || entry.index < 0 || Math.floor(entry.index) !== entry.index) return []
  if (entry.source !== "pin" && entry.source !== "history") return []
  return [verb, entry.source === "pin" ? "--pin" : "--history", String(entry.index),
          "--fingerprint", entry.fp]
}

// ---------------------------------------------------------------- preview

function collapse(text, limit) {
  var max = limit === undefined ? 120 : limit
  // Whitespace is collapsed rather than preserved: a row is one line high, and a
  // snippet that starts with three blank lines would otherwise look like an empty row.
  var flat = String(text === undefined || text === null ? "" : text)
    .replace(/\s+/g, " ")
    .trim()
  if (flat.length <= max) return flat
  return flat.slice(0, max - 1) + "…"
}

function previewOf(entry, limit) {
  if (!entry) return ""
  if (String(entry.type) === "image") {
    var name = String(entry.name || entry.path || "").split("/").pop()
    return name || "Image"
  }
  return collapse(entry.text, limit)
}

// A stable identity for an entry. The helper fingerprints the full text (or path), so the
// same snippet in history and in pins shares one key even though both are truncated here.
function keyOf(entry) {
  if (!entry) return ""
  if (entry.fp) return "fp:" + String(entry.fp)
  if (String(entry.type) === "image") return "image:" + String(entry.path || entry.name || "")
  return "text:" + String(entry.text || "")
}

function isPinned(pins, entry) {
  var key = keyOf(entry)
  for (var i = 0; i < pins.length; i++) if (keyOf(pins[i]) === key) return true
  return false
}

// ---------------------------------------------------------------- rows

function matches(entry, needle) {
  if (!needle) return true
  var hay = (String(entry.text || "") + " " + String(entry.name || entry.path || "") + " " +
             classify(entry) + " " + String(entry.label || "")).toLowerCase()
  return hay.indexOf(needle.toLowerCase()) >= 0
}

// Pinned first, then history with anything already pinned removed, so nothing appears
// twice and the numbers stay stable while you type.
function buildRows(history, pins, query, limit) {
  var max = limit === undefined ? 60 : limit
  var rows = []
  var seen = {}
  var i, entry

  var pinList = Array.isArray(pins) ? pins : []
  for (i = 0; i < pinList.length; i++) {
    entry = pinList[i]
    if (!entry) continue
    if (!matches(entry, query)) continue
    var pk = keyOf(entry)
    if (seen[pk]) continue
    seen[pk] = true
    rows.push({ entry: entry, pinned: true, kind: classify(entry),
                glyph: glyphFor(classify(entry)), swatch: swatchFor(entry),
                preview: previewOf(entry), key: pk })
    if (rows.length >= max) return rows
  }

  var list = Array.isArray(history) ? history : []
  for (i = 0; i < list.length; i++) {
    entry = list[i]
    if (!entry) continue
    var k = keyOf(entry)
    if (seen[k]) continue
    if (!matches(entry, query)) continue
    seen[k] = true
    rows.push({ entry: entry, pinned: false, kind: classify(entry),
                glyph: glyphFor(classify(entry)), swatch: swatchFor(entry),
                preview: previewOf(entry), key: k })
    if (rows.length >= max) break
  }
  return rows
}

function clampIndex(index, count) {
  if (count <= 0) return 0
  var n = Math.round(Number(index))
  if (!isFinite(n)) return 0
  if (n < 0) return count - 1      // wrap, so Up from the top lands on the last row
  if (n >= count) return 0
  return n
}
