import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "ShelfModel.js" as Shelf

Panel {
  id: root
  moduleName: "reidenxerx.clipboard-shelf"
  ipcTarget: "reidenxerx.clipboard-shelf"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root

  readonly property string home: Quickshell.env("HOME")
  // Omarchy's clipboard service already runs two persistent `wl-paste --watch` capture
  // processes and already drops password-manager content. Reading its history instead of
  // starting a second watcher means no double capture, the same entries the stock picker
  // shows, and that sensitive-content filtering applies here for free.
  readonly property string historyPath: home + "/.local/state/omarchy/clipboard-history.json"
  readonly property string pinsPath: home + "/.config/omarchy/clipboard-shelf.json"

  property var history: []
  property var pins: []
  property string query: ""
  property int selected: 0

  // Hover preview: which row the pointer is on, and where that row sits in panel
  // coordinates so the card can line up with it.
  property int previewIndex: -1
  property real previewY: 0
  readonly property var previewRow: previewIndex >= 0 && previewIndex < rows.length
    ? rows[previewIndex] : null
  readonly property bool previewVisible: !!previewRow && previewRow.kind === "image"

  readonly property int maxRows: Number(setting("maxRows", 40))
  readonly property int previewLength: Number(setting("previewLength", 120))
  readonly property bool showGlyph: setting("showGlyph", true) !== false
  readonly property int pinCount: pins.length

  readonly property var rows: Shelf.buildRows(history, pins, query, maxRows)

  // ---------------------------------------------------------------- data

  FileView {
    id: historyFile
    path: root.historyPath
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      try { root.history = JSON.parse(text()) || [] } catch (e) { root.history = [] }
    }
    onLoadFailed: root.history = []
  }

  FileView {
    id: pinsFile
    path: root.pinsPath
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      var parsed = null
      try { parsed = JSON.parse(text()) } catch (e) { parsed = null }
      root.pins = Shelf.normalizePins(parsed)
    }
    onLoadFailed: root.pins = []
  }

  Process { id: writer }

  function savePins(next) {
    root.pins = next
    // Written through the CLI so the panel and `clip-shelf` can never disagree about the
    // file format, and so the write is atomic (temp file then rename) rather than a
    // half-written file the watcher might read back as invalid.
    writer.command = [root.pluginBin + "clip-shelf", "write", JSON.stringify({ pins: next })]
    writer.running = true
  }

  readonly property string pluginBin: String(Qt.resolvedUrl("bin/")).replace("file://", "")

  // ---------------------------------------------------------------- actions

  Process { id: copier }

  function copyRow(index) {
    var row = rows[index]
    if (!row) return
    if (row.entry.type === "image") copier.command = ["sh", "-c", "wl-copy < " + shellQuote(row.entry.path)]
    else copier.command = ["sh", "-c", "printf %s " + shellQuote(String(row.entry.text || "")) + " | wl-copy"]
    copier.running = true
    root.close()
  }

  function shellQuote(value) {
    return "'" + String(value).replace(/'/g, "'\\''") + "'"
  }

  function togglePinAt(index) {
    var row = rows[index]
    if (!row) return
    savePins(Shelf.togglePin(pins, row.entry))
  }

  function moveSelection(delta) {
    root.selected = Shelf.clampIndex(root.selected + delta, rows.length)
  }

  onOpenedChanged: {
    if (opened) {
      root.query = ""
      root.selected = 0
      historyFile.reload()
      pinsFile.reload()
      root.previewIndex = -1
      searchField.forceActiveFocus()
    }
  }
  onQueryChanged: { root.selected = 0; root.previewIndex = -1 }

  // ---------------------------------------------------------------- ui

  // Content lives inside a KeyboardPanel, which owns the popup chrome, border, placement
  // and focus handling. focusTarget is the search field rather than a PanelKeyCatcher:
  // that catcher maps j/k/h/l to navigation and x to delete, which is right for a panel
  // you only steer, and wrong for one you type into -- "ssh" would move the cursor twice
  // instead of filtering.
  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    centerOnBar: true
    focusTarget: searchField
    popoutSwitching: root.popoutSwitching
    popoutSwitchClosing: root.popoutSwitchClosing
    contentWidth: panel.fittedContentWidth(Style.space(560))
    contentHeight: panel.fittedContentHeight(column.implicitHeight)

  ColumnLayout {
    id: column
    width: parent.width
    spacing: Style.space(10)

    // ---- search
    Rectangle {
      Layout.fillWidth: true
      implicitHeight: Style.space(34)
      radius: Style.cornerRadius
      color: Style.hoverFillFor(root.barForeground, Color.accent)

      RowLayout {
        anchors.fill: parent
        anchors.leftMargin: Style.space(10)
        anchors.rightMargin: Style.space(10)
        spacing: Style.space(8)

        Text {
          text: "󰍉"
          color: root.barForeground
          opacity: 0.6
          font.pixelSize: Style.font.body
        }

        TextInput {
          id: searchField
          Layout.fillWidth: true
          color: root.barForeground
          font.pixelSize: Style.font.body
          font.family: root.bar ? root.bar.fontFamily : Style.font.family
          selectByMouse: true
          clip: true
          onTextChanged: root.query = text

          Text {
            anchors.verticalCenter: parent.verticalCenter
            visible: searchField.text === ""
            text: "Type to filter — 1-9 pastes, Ctrl+P pins"
            color: root.barForeground
            opacity: 0.4
            font: searchField.font
          }

          Keys.onPressed: function (event) {
            if (event.key === Qt.Key_Down)      { root.moveSelection(1);  event.accepted = true }
            else if (event.key === Qt.Key_Up)   { root.moveSelection(-1); event.accepted = true }
            else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
              root.copyRow(root.selected); event.accepted = true
            } else if (event.key === Qt.Key_Escape) { root.close(); event.accepted = true }
            else if (event.key === Qt.Key_P && (event.modifiers & Qt.ControlModifier)) {
              root.togglePinAt(root.selected); event.accepted = true
            } else if ((event.modifiers & Qt.AltModifier) &&
                       event.key >= Qt.Key_1 && event.key <= Qt.Key_9) {
              // Alt rather than bare digits: the search field has to stay typeable, and
              // a bare "1" would paste instead of filtering for it.
              root.copyRow(event.key - Qt.Key_1); event.accepted = true
            }
          }
        }
      }
    }

    // ---- rows
    ListView {
      id: list
      Layout.fillWidth: true
      // Derived from the rows rather than filling: the panel's height comes from this
      // column's implicitHeight, so a fillHeight child here is a binding loop.
      Layout.preferredHeight: Math.min(Math.max(root.rows.length, 1) * Style.space(34),
                                       Style.space(380))
      clip: true
      model: root.rows
      currentIndex: root.selected
      highlightMoveDuration: 90
      onCurrentIndexChanged: positionViewAtIndex(currentIndex, ListView.Contain)

      delegate: Rectangle {
        width: list.width
        implicitHeight: Style.space(34)
        radius: Style.cornerRadius
        color: index === root.selected
          ? Style.hoverFillFor(root.barForeground, Color.accent) : "transparent"

        RowLayout {
          anchors.fill: parent
          anchors.leftMargin: Style.space(8)
          anchors.rightMargin: Style.space(8)
          spacing: Style.space(8)

          // Position, so Alt+N is readable off the row rather than counted.
          Text {
            visible: index < 9
            text: String(index + 1)
            color: root.barForeground
            opacity: 0.35
            font.pixelSize: Style.font.caption
            Layout.preferredWidth: Style.space(12)
          }

          // A colour shows itself; everything else shows what kind of thing it is.
          Rectangle {
            visible: modelData.kind === "color"
            width: Style.space(14); height: Style.space(14)
            radius: Math.min(3, Style.cornerRadius)
            color: modelData.swatch || "transparent"
            border.width: 1
            border.color: Qt.rgba(root.barForeground.r, root.barForeground.g, root.barForeground.b, 0.3)
          }
          Text {
            visible: root.showGlyph && modelData.kind !== "color"
            text: modelData.glyph
            color: root.barForeground
            opacity: 0.65
            font.pixelSize: Style.font.body
            Layout.preferredWidth: Style.space(16)
          }

          Text {
            Layout.fillWidth: true
            text: Shelf.collapse(modelData.preview, root.previewLength)
            color: root.barForeground
            font.pixelSize: Style.font.body
            elide: Text.ElideRight
          }

          Text {
            visible: modelData.pinned
            text: "󰐃"
            color: Color.accent
            font.pixelSize: Style.font.caption
          }
        }

        MouseArea {
          anchors.fill: parent
          acceptedButtons: Qt.LeftButton | Qt.RightButton
          hoverEnabled: true
          onEntered: {
            root.selected = index
            root.previewIndex = index
            // Panel coordinates, so the preview lines up with the row no matter how far
            // the list is scrolled.
            root.previewY = parent.mapToItem(column, 0, 0).y
          }
          onExited: if (root.previewIndex === index) root.previewIndex = -1
          onClicked: function (mouse) {
            if (mouse.button === Qt.RightButton) root.togglePinAt(index)
            else root.copyRow(index)
          }
        }
      }
    }


    // ---- image preview, floated to the left of the panel
    //
    // KeyboardPanel is a full-screen layer-shell surface with the card placed inside it,
    // and neither the card nor its content holder clips. So a negative x here paints
    // beside the panel instead of being cut off, and no second popup window is needed.
    Item {
      id: previewCard
      parent: column
      visible: root.previewVisible
      width: Style.space(240)
      height: Style.space(190)
      x: -(width + Style.space(12))
      // Centred on the hovered row, then held inside the panel so a row near either end
      // does not push the card off the top of the screen.
      y: Math.max(0, Math.min(column.height - height,
                              root.previewY - height / 2 + Style.space(17)))
      z: 10
      opacity: root.previewVisible ? 1 : 0
      Behavior on opacity { NumberAnimation { duration: 110; easing.type: Easing.OutCubic } }

      Rectangle {
        anchors.fill: parent
        radius: Style.cornerRadius
        color: Color.tooltip.background
        border.width: Style.normalBorderWidth
        border.color: Color.tooltip.border

        Column {
          anchors.fill: parent
          anchors.margins: Style.space(8)
          spacing: Style.space(6)

          Image {
            id: previewImage
            width: parent.width
            height: parent.height - previewCaption.height - Style.space(6)
            source: root.previewRow && root.previewRow.entry.path
              ? "file://" + root.previewRow.entry.path : ""
            fillMode: Image.PreserveAspectFit
            asynchronous: true
            cache: false
            // Decode at display size: clipboard screenshots are full-resolution panels,
            // and decoding 2560x1600 for a 240px card on every hover is wasteful.
            sourceSize.width: Style.space(240)
            smooth: true
          }

          Text {
            id: previewCaption
            width: parent.width
            text: previewImage.status === Image.Ready
              ? previewImage.implicitWidth + "×" + previewImage.implicitHeight
              : (previewImage.status === Image.Error ? "Preview unavailable" : "Loading…")
            color: Color.tooltip.text
            opacity: 0.6
            font.pixelSize: Style.font.caption
            horizontalAlignment: Text.AlignHCenter
            elide: Text.ElideRight
          }
        }
      }
    }

    // ---- empty state
    Text {
      Layout.fillWidth: true
      visible: root.rows.length === 0
      horizontalAlignment: Text.AlignHCenter
      text: root.query !== "" ? "Nothing matches “" + root.query + "”"
                              : "Nothing copied yet — the shelf fills as you go"
      color: root.barForeground
      opacity: 0.5
      font.pixelSize: Style.font.bodySmall
    }

    // ---- footer
    Text {
      Layout.fillWidth: true
      text: root.pinCount > 0
        ? root.pinCount + " pinned · " + root.rows.length + " shown · right-click a row to pin"
        : root.rows.length + " shown · right-click a row to pin it"
      color: root.barForeground
      opacity: 0.4
      font.pixelSize: Style.font.caption
      elide: Text.ElideRight
    }
  }
  }
}
