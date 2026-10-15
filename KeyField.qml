import QtQuick
import Quickshell.Io

// A text field that holds only what is typed into it.
//
// Qt's own text controls read whatever another app puts on the clipboard as soon as it changes, with no limit on its
// size and no deadline, and paste it on Ctrl+V or a middle click the same way. In the shared shell that would let any
// app stall the shell or fill its memory, so this field draws its text itself and reads keys only. A paste (Ctrl+V or
// Shift+Insert) is the one time it reads the clipboard: text only, at most pasteBytes of it, within two seconds,
// through wl-paste.
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
      else if (c >= 32 && c !== 127 && c !== 0xfffd) clean += raw[i]
    }
    var next = (field.text + clean).slice(0, Math.max(0, maximumLength))
    var last = next.charCodeAt(next.length - 1)
    if (last >= 0xd800 && last <= 0xdbff) next = next.slice(0, -1)
    field.text = next
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
    y: field.multiline ? 0 : (field.height - height) / 2
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
    command: ["/usr/bin/sh", "-c", "/usr/bin/timeout -k 1 2 /usr/bin/wl-paste --no-newline --type text 2>/dev/null | /usr/bin/head -c "
              + field.pasteBytes]
    stdout: StdioCollector {
      onStreamFinished: if (!field.readOnly) field.insert(text)
    }
  }
}
