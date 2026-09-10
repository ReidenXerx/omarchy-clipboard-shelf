.pragma library

// Pure logic for the clipboard shelf. No QML, no I/O, so it can be exercised with node.

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
    var name = String(entry.path || "").split("/").pop()
    return name || "Image"
  }
  return collapse(entry.text, limit)
}

// A stable identity for an entry, so a pin can point at one and survive a restart.
function keyOf(entry) {
  if (!entry) return ""
  if (String(entry.type) === "image") return "image:" + String(entry.path || "")
  return "text:" + String(entry.text || "")
}

// ---------------------------------------------------------------- pins

function normalizePins(raw) {
  var list = []
  var source = raw && Array.isArray(raw.pins) ? raw.pins : (Array.isArray(raw) ? raw : [])
  for (var i = 0; i < source.length; i++) {
    var p = source[i]
    if (!p) continue
    if (typeof p === "string") { list.push({ type: "text", text: p }); continue }
    if (typeof p !== "object") continue
    if (String(p.type) === "image" && p.path) list.push({ type: "image", path: String(p.path), mime: String(p.mime || "image/png") })
    else if (p.text !== undefined) list.push({ type: "text", text: String(p.text), label: p.label ? String(p.label) : "" })
  }
  return list
}

function isPinned(pins, entry) {
  var key = keyOf(entry)
  for (var i = 0; i < pins.length; i++) if (keyOf(pins[i]) === key) return true
  return false
}

function togglePin(pins, entry) {
  var key = keyOf(entry)
  var out = []
  var found = false
  for (var i = 0; i < pins.length; i++) {
    if (keyOf(pins[i]) === key) { found = true; continue }
    out.push(pins[i])
  }
  // New pins go on top: the thing you just decided to keep is the thing you are about
  // to want, and pushing it to the end of a long shelf hides it.
  if (!found) out.unshift(entry)
  return out
}

// ---------------------------------------------------------------- rows

function matches(entry, needle) {
  if (!needle) return true
  var hay = (String(entry.text || "") + " " + String(entry.path || "") + " " +
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

  for (i = 0; i < pins.length; i++) {
    entry = pins[i]
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
