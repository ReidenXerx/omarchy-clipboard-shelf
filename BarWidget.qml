import QtQuick
import qs.Commons
import qs.Ui

// Bar slot for the shelf. Follows the shape Omarchy's own panel widgets use: the panel
// lives in a Loader here, and the bar identifies the panel by THIS widget, not by the
// nested item, so open/close/opened have to be forwarded.
BarWidget {
  id: root
  moduleName: "reidenxerx.clipboard-shelf"

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }

  function togglePanel() {
    if (panelLoader.item && panelLoader.item.toggle) panelLoader.item.toggle()
  }

  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  function open() { if (panelLoader.item && panelLoader.item.open) panelLoader.item.open() }
  function close() { if (panelLoader.item && panelLoader.item.close) panelLoader.item.close() }

  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false
  function closeForPopoutSwitch() { if (panelLoader.item) panelLoader.item.closeForPopoutSwitch() }

  readonly property int pinCount: panelLoader.item ? panelLoader.item.pinCount : 0

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "󰅌"
    // No slotSize override: BarIconButton defaults to Style.bar.iconSlot (27), which is
    // what the rest of the bar uses. statusSlot is 21, and setting it made this widget
    // six logical pixels narrower than its neighbours -- visible as a tighter gap.
    // Plain bar foreground, like every other status glyph. An accent tint here reads as
    // a warning rather than as information, and the bar is calmer when one icon does not
    // shout at you about a state you already chose.
    foreground: root.bar ? root.bar.barForeground : Color.foreground
    tooltipText: root.pinCount > 0
      ? root.pinCount + (root.pinCount === 1 ? " pinned snippet" : " pinned snippets")
      : "Clipboard shelf"

    onPressed: function (b) {
      if (b === Qt.RightButton && root.bar) root.bar.run("omarchy-menu-open setup.clipboard-shelf")
      else root.togglePanel()
    }
  }
}
