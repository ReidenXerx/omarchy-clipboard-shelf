import QtQuick
import Quickshell
import Quickshell.Io

// A text field that holds only what is typed into it.
//
// Qt's own text controls read whatever another app puts on the clipboard as soon as it changes, with no limit on its
// size and no deadline, and paste it on Ctrl+V or a middle click the same way. In the shared shell that would let any
// app stall the shell or fill its memory, so this field draws its text itself and reads keys only. A paste (Ctrl+V or
// Shift+Insert) is the one time it reads the clipboard: text only, at most pasteBytes of it, within two seconds,
// through wl-paste, with nothing of the shell's environment but what wl-paste needs to reach the compositor.
FocusScope {
  id: field

  property string text: ""
  property string placeholderText: ""
  property int maximumLength: 512
  property bool secret: false
  property bool multiline: false
  property bool pasteEnabled: true
  // Keys still reach keyFilter, but the text stays as it is.
  property bool readOnly: false
  property color color: "white"
  property color placeholderColor: Qt.rgba(color.r, color.g, color.b, 0.5)
  property alias font: shown.font
  // Sees each key first, as Keys.onPressed would on Qt's own fields: setting event.accepted takes the key.
  property var keyFilter: null
  readonly property int pasteBytes: 16384
  readonly property bool pasting: paster.running
  readonly property real contentHeight: shown.contentHeight

  signal accepted()
  signal editingFinished()

  implicitWidth: 160
  implicitHeight: multiline ? Math.max(shown.contentHeight, metrics.height) : metrics.height
  activeFocusOnTab: true
  clip: true

  function clear() { text = "" }

  // Typed or pasted text, without control characters (a line break stays only in a multi-line field), cut to
  // maximumLength without splitting a character in two.
  function insert(s) {
    var clean = ""
    var raw = String(s)
    for (var i = 0; i < raw.length; i++) {
      var c = raw.charCodeAt(i)
      if (c === 10 || c === 13 || c === 9) clean += multiline && c === 10 ? raw[i] : (c === 13 ? "" : " ")
      // Left out: control characters (C0, DEL, C1), the replacement character a cut-off paste ends in, line and
      // paragraph separators, and the invisible marks that reorder text (bidi embeddings, overrides and isolates).
      else if (c >= 32 && c !== 127 && !(c >= 0x80 && c <= 0x9f) && c !== 0xfffd
               && !(c >= 0x2028 && c <= 0x202e) && !(c >= 0x2066 && c <= 0x2069)) clean += raw[i]
    }
    // Only what is added is cut to fit: text already there (a long saved value) is never shortened by a keystroke.
    var add = clean.slice(0, Math.max(0, maximumLength - field.text.length))
    var last = add.charCodeAt(add.length - 1)
    if (last >= 0xd800 && last <= 0xdbff) add = add.slice(0, -1)
    if (add) field.text = field.text + add
  }

  function dropChar(s) {
    if (s.length === 0) return s
    var last = s.charCodeAt(s.length - 1)
    var before = s.length > 1 ? s.charCodeAt(s.length - 2) : 0
    return s.slice(0, last >= 0xdc00 && last <= 0xdfff && before >= 0xd800 && before <= 0xdbff ? -2 : -1)
  }

  function dropWord(s) {
    var end = s.length
    while (end > 0 && s[end - 1] === " ") end--
    while (end > 0 && s[end - 1] !== " ") end--
    return s.slice(0, end)
  }

  function consumes(event) {
    var k = event.key
    if ([Qt.Key_Delete, Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down, Qt.Key_Home, Qt.Key_End].indexOf(k) >= 0)
      return true
    return (event.modifiers & Qt.ControlModifier) !== 0
      && [Qt.Key_A, Qt.Key_C, Qt.Key_X, Qt.Key_Z, Qt.Key_Y, Qt.Key_U, Qt.Key_K, Qt.Key_W].indexOf(k) >= 0
  }

  function paste() {
    if (field.pasteEnabled && !field.readOnly && !paster.running) paster.running = true
  }

  Keys.onPressed: function (event) {
    event.accepted = false
    if (typeof field.keyFilter === "function") {
      field.keyFilter(event)
      if (event.accepted) return
    }
    if (field.readOnly) return
    var mods = event.modifiers
    var ctrl = (mods & Qt.ControlModifier) !== 0
    if ((ctrl && event.key === Qt.Key_V) || ((mods & Qt.ShiftModifier) && event.key === Qt.Key_Insert)) {
      event.accepted = true
      field.paste()
    } else if (event.key === Qt.Key_Backspace) {
      event.accepted = true
      field.text = ctrl ? field.dropWord(field.text) : field.dropChar(field.text)
    } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
      event.accepted = true
      if (field.multiline && (mods & Qt.ShiftModifier)) {
        field.insert(String.fromCharCode(10))
      } else {
        field.accepted()
        field.editingFinished()
      }
    } else if (!ctrl && (mods & (Qt.AltModifier | Qt.MetaModifier)) === 0 && event.text.length > 0
               && event.text.charCodeAt(0) >= 32 && event.text.charCodeAt(0) !== 127) {
      event.accepted = true
      field.insert(event.text)
    } else if (field.consumes(event)) {
      // Keys a text field owns go no further, as with Qt's own fields: Delete, arrows, Home and End, and the Ctrl
      // editing shortcuts must not reach whatever is behind the field (a list, a bar editor, a window's undo).
      event.accepted = true
    }
  }

  onActiveFocusChanged: if (!activeFocus) field.editingFinished()

  FontMetrics { id: metrics; font: shown.font }

  Text {
    id: shown
    // Whatever was typed or pasted, drawn as it is: never read as markup.
    textFormat: Text.PlainText
    text: (field.secret ? String.fromCodePoint(0x2022).repeat(field.text.length) : field.text)
          + (field.multiline && field.activeFocus ? String.fromCodePoint(0x258f) : "")
    color: field.color
    wrapMode: field.multiline ? Text.Wrap : Text.NoWrap
    width: field.multiline ? field.width : implicitWidth
    // One line keeps its end in view, where the cursor is.
    x: field.multiline ? 0 : Math.min(0, field.width - caret.width - implicitWidth)
    // A long text keeps its last lines, where the caret is, in view; one line keeps its end in view the same way.
    y: field.multiline ? Math.min(0, field.height - contentHeight) : (field.height - height) / 2
  }

  Text {
    textFormat: Text.PlainText
    visible: field.text === ""
    width: field.width
    elide: Text.ElideRight
    y: field.multiline ? 0 : (field.height - height) / 2
    text: field.placeholderText
    color: field.placeholderColor
    font: shown.font
  }

  Rectangle {
    id: caret
    visible: !field.multiline && field.activeFocus && blink.on
    x: field.text === "" ? 0 : shown.x + shown.implicitWidth
    width: Math.max(1, Math.round(metrics.height / 16))
    height: metrics.height
    y: (field.height - height) / 2
    color: field.color
  }

  Timer {
    id: blink
    property bool on: true
    interval: 530
    repeat: true
    running: field.activeFocus && !field.multiline
    onRunningChanged: on = true
    onTriggered: on = !on
  }

  MouseArea {
    anchors.fill: parent
    acceptedButtons: Qt.LeftButton
    cursorShape: Qt.IBeamCursor
    onClicked: field.forceActiveFocus()
  }

  Process {
    id: paster
    clearEnvironment: true
    environment: ({
      PATH: "/usr/bin",
      XDG_RUNTIME_DIR: Quickshell.env("XDG_RUNTIME_DIR") || "",
      WAYLAND_DISPLAY: Quickshell.env("WAYLAND_DISPLAY") || ""
    })
    command: ["/usr/bin/sh", "-c", "/usr/bin/timeout -k 1 2 /usr/bin/wl-paste --no-newline --type text 2>/dev/null | /usr/bin/head -c "
              + field.pasteBytes]
    stdout: StdioCollector {
      // A paste that arrives after you moved on is dropped rather than typed into a field you left.
      onStreamFinished: if (!field.readOnly && field.activeFocus) field.insert(text)
    }
  }

  // timeout(1) bounds wl-paste; this bounds the whole pipeline, so a paste that never ends cannot block the next.
  Timer {
    interval: 4000
    running: paster.running
    onTriggered: if (paster.running) paster.signal(9)
  }
}
