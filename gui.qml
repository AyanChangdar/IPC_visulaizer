import QtQuick 2.15
import QtQuick.Controls.Basic
import QtQuick.Layouts 1.15

ApplicationWindow {
    id: win
    visible: true
    width: 1160
    height: 780
    minimumWidth: 850
    minimumHeight: 630
    title: "IPC NEXUS | Inter-Process Communication Monitor"
    color: "#0a101b"

    palette.window: "#192639"
    palette.windowText: "#f0f7ff"
    palette.base: "#202f44"
    palette.text: "#f0f7ff"
    palette.button: "#283b52"
    palette.buttonText: "#f5fbff"
    palette.highlight: "#147d78"
    palette.highlightedText: "#f5fbff"

    readonly property color panel: "#192639"
    readonly property color card: "#202f44"
    readonly property color cyan: "#5bc7d8"
    readonly property color muted: "#a0b2ca"
    readonly property color green: "#72e5b0"
    property int transferSource: -1
    property int transferDestination: -1
    property int deliveryId: 0
    readonly property bool deadlockDetected: backend.frozenProcesses.indexOf(2) !== -1
                                              && backend.frozenProcesses.indexOf(3) !== -1

    function metricsFor(index) {
        return index < backend.processMetrics.length ? backend.processMetrics[index] : null
    }

    function nodeX(i, n, w) {
        if (n === 4) return (i === 0 || i === 3) ? w * 0.23 : w * 0.77
        return w * (0.5 + 0.37 * Math.cos(-Math.PI / 2 + 2 * Math.PI * i / n))
    }
    function nodeY(i, n, h) {
        if (n === 4) return (i < 2) ? h * 0.29 : h * 0.76
        return h * (0.5 + 0.35 * Math.sin(-Math.PI / 2 + 2 * Math.PI * i / n))
    }
    function fireTransfer(src, dst) {
        if (!backend.running || src < 0 || dst < 0 || src >= procSpin.value || dst >= procSpin.value) return
        transferSource = src
        transferDestination = dst
        activeTransfers.append({transferId: ++deliveryId, sourceIndex: src, destinationIndex: dst})
        flowCanvas.requestPaint()
    }
    function removeTransfer(id) {
        for (var i = 0; i < activeTransfers.count; i++) {
            if (activeTransfers.get(i).transferId === id) {
                activeTransfers.remove(i)
                return
            }
        }
    }

    ListModel { id: activeTransfers }
    Connections {
        target: backend
        function onDataFlow(src, dst) { win.fireTransfer(src, dst) }
        function onRunningChanged() {
            if (!backend.running) {
                activeTransfers.clear()
                transferSource = -1
                transferDestination = -1
            }
            flowCanvas.requestPaint()
        }
    }

    component NexusButton: Button {
        id: btn
        property color fillColor: "#283b52"
        property color disabledFill: "#293345"
        implicitHeight: 39
        implicitWidth: 92
        font.pixelSize: 13
        font.bold: true
        background: Rectangle {
            radius: 8
            color: btn.enabled ? (btn.down ? Qt.darker(btn.fillColor, 1.18) : btn.fillColor) : btn.disabledFill
            border.color: btn.enabled ? Qt.lighter(btn.fillColor, 1.2) : "#3a4656"
            border.width: 1
        }
        contentItem: Text {
            text: btn.text
            color: btn.enabled ? "#f5fbff" : "#8494a8"
            font: btn.font
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 18
        spacing: 12

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 54
            color: "transparent"
            RowLayout {
                anchors.fill: parent
                spacing: 12
                Rectangle {
                    width: 40; height: 40; radius: 12
                    color: "#153d4c"; border.color: "#357d91"
                    Text { anchors.centerIn: parent; text: "⌘"; color: win.cyan; font.pixelSize: 24; font.bold: true }
                }
                ColumnLayout {
                    spacing: 2
                    Text { text: "IPC NEXUS"; font.pixelSize: 22; font.bold: true; color: "#f0f7ff"; font.letterSpacing: 1 }
                    Text { text: "Inter-Process Communication Monitor"; font.pixelSize: 11; color: win.muted }
                }
                Item { Layout.fillWidth: true }
                Rectangle {
                    implicitWidth: stateText.implicitWidth + 26
                    height: 29; radius: 14
                    color: backend.running ? "#153f36" : "#283446"
                    border.color: backend.running ? "#2d886b" : "#40546b"
                    Text {
                        id: stateText
                        anchors.centerIn: parent
                        text: backend.running ? "●  SIMULATION ACTIVE" : "●  READY"
                        color: backend.running ? win.green : win.muted
                        font.pixelSize: 11; font.bold: true
                    }
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 65
            radius: 12; color: win.panel
            border.color: "#29384f"
            RowLayout {
                anchors.fill: parent; anchors.margins: 10; spacing: 9
                Text { text: "PROCESSES"; color: win.muted; font.pixelSize: 10; font.bold: true }
                SpinBox {
                    id: procSpin
                    objectName: "processCount"
                    from: 2; to: 20; value: 4
                    onValueChanged: {
                        if (value < 4 && backend.deadlockActive && !backend.running) backend.toggleDeadlock()
                    }
                    enabled: !backend.running
                    Layout.preferredWidth: 90
                    editable: true
                }
                Text { text: "IPC MODE"; color: win.muted; font.pixelSize: 10; font.bold: true }
                ComboBox {
                    id: ipcCombo
                    objectName: "ipcMode"
                    model: ["Pipe", "Message Queue", "Shared Memory"]
                    enabled: !backend.running
                    Layout.preferredWidth: 153
                    onCurrentIndexChanged: {
                        if (currentIndex !== 2 && backend.deadlockActive && !backend.running) backend.toggleDeadlock()
                    }
                }
                NexusButton {
                    objectName: "startButton"
                    text: "▶  Start"
                    fillColor: "#147d78"
                    enabled: !backend.running
                    onClicked: backend.start(procSpin.value, ipcCombo.currentIndex)
                }
                NexusButton {
                    objectName: "stopButton"
                    text: "■  Stop"
                    fillColor: "#983f50"
                    enabled: backend.running
                    onClicked: backend.stop()
                }
                NexusButton {
                    objectName: "deadlockButton"
                    text: backend.deadlockActive ? "Deadlock Armed" : "Force Deadlock"
                    implicitWidth: 120
                    fillColor: backend.deadlockActive ? "#a44d36" : "#86613a"
                    enabled: !backend.running && ipcCombo.currentIndex === 2 && procSpin.value >= 4
                    onClicked: backend.toggleDeadlock()
                    ToolTip.visible: hovered
                    ToolTip.text: "Enable before Start with Shared Memory and at least four workers"
                }
                Item { Layout.fillWidth: true }
                NexusButton {
                    text: "Export Log"
                    fillColor: "#374c66"
                    onClicked: backend.exportLog()
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumWidth: 390
                radius: 14; color: win.panel
                border.color: "#29384f"
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 16; spacing: 7
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: "Live IPC Flow"; color: "#f0f7ff"; font.pixelSize: 16; font.bold: true }
                        Item { Layout.fillWidth: true }
                        Text { text: backend.running ? "LIVE" : "STANDBY"; color: backend.running ? win.green : win.muted; font.pixelSize: 11; font.bold: true }
                    }
                    Text { text: "Process-to-process message routing"; color: win.muted; font.pixelSize: 11 }
                    Item {
                        id: flowArea
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Canvas {
                            id: flowCanvas
                            anchors.fill: parent
                            onWidthChanged: requestPaint()
                            onHeightChanged: requestPaint()
                            onPaint: {
                                var ctx = getContext("2d")
                                ctx.clearRect(0, 0, width, height)
                                if (win.transferSource < 0 || win.transferDestination < 0) return
                                var n = procSpin.value
                                var sx = win.nodeX(win.transferSource, n, width)
                                var sy = win.nodeY(win.transferSource, n, height)
                                var ex = win.nodeX(win.transferDestination, n, width)
                                var ey = win.nodeY(win.transferDestination, n, height)
                                var dx = ex - sx, dy = ey - sy
                                var len = Math.sqrt(dx*dx + dy*dy)
                                if (len <= 86) return
                                var ux = dx/len, uy = dy/len
                                var ax = ex - ux*43, ay = ey - uy*43
                                ctx.lineWidth = 2.3
                                ctx.strokeStyle = "#4e9eaf"
                                ctx.beginPath()
                                ctx.moveTo(sx + ux*43, sy + uy*43)
                                ctx.lineTo(ax, ay)
                                ctx.stroke()
                                ctx.beginPath()
                                ctx.moveTo(ax, ay)
                                ctx.lineTo(ax - ux*11 - uy*5, ay - uy*11 + ux*5)
                                ctx.lineTo(ax - ux*11 + uy*5, ay - uy*11 - ux*5)
                                ctx.closePath()
                                ctx.fillStyle = ctx.strokeStyle
                                ctx.fill()
                            }
                        }
                        Repeater {
                            model: procSpin.value
                            delegate: Rectangle {
                                id: processNode
                                required property int index
                                objectName: "processNode" + index
                                readonly property var stats: win.metricsFor(index)
                                readonly property bool blocked: backend.frozenProcesses.indexOf(index) !== -1
                                width: procSpin.value > 10 ? 62 : 84
                                height: procSpin.value > 10 ? 55 : 72
                                x: win.nodeX(index, procSpin.value, flowArea.width) - width/2
                                y: win.nodeY(index, procSpin.value, flowArea.height) - height/2
                                radius: 13
                                color: blocked ? "#603341" : "#273d53"
                                border.width: 2
                                border.color: blocked ? "#ff7e83" : win.cyan
                                Column {
                                    anchors.centerIn: parent
                                    spacing: 3
                                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: "P" + index; font.pixelSize: procSpin.value > 10 ? 16 : 22; font.bold: true; color: "#f5fbff" }
                                    Text {
                                        anchors.horizontalCenter: parent.horizontalCenter
                                        text: {
                                            if (processNode.blocked) return "Waiting"
                                            if (!backend.running) return "Idle"
                                            if (!processNode.stats) return "Starting"
                                            if (!processNode.stats.alive) return "Exited"
                                            return processNode.stats.status ?? "Starting"
                                        }
                                        color: processNode.blocked ? "#ff9e9e" : (processNode.stats && processNode.stats.alive ? win.green : win.muted)
                                        font.pixelSize: 10
                                    }
                                }
                            }
                        }
                        Repeater {
                            model: activeTransfers
                            delegate: Rectangle {
                                id: packet
                                required property int transferId
                                required property int sourceIndex
                                required property int destinationIndex
                                property real progress: 0
                                property real startX: win.nodeX(sourceIndex, procSpin.value, flowArea.width)
                                property real startY: win.nodeY(sourceIndex, procSpin.value, flowArea.height)
                                width: 12; height: 12; radius: 6
                                color: "#ffdc79"
                                x: startX + (win.nodeX(destinationIndex, procSpin.value, flowArea.width) - startX) * progress - width / 2
                                y: startY + (win.nodeY(destinationIndex, procSpin.value, flowArea.height) - startY) * progress - height / 2
                                NumberAnimation on progress {
                                    from: 0; to: 1
                                    duration: 650
                                    easing.type: Easing.InOutQuad
                                    onFinished: win.removeTransfer(packet.transferId)
                                }
                            }
                        }
                    }
                    RowLayout {
                        objectName: "deadlockLocks"
                        visible: win.deadlockDetected
                        Layout.fillWidth: true
                        Repeater {
                            model: ["Lock A · P2 owns · P3 waits", "Lock B · P3 owns · P2 waits"]
                            delegate: Rectangle {
                                required property string modelData
                                Layout.fillWidth: true
                                implicitHeight: lockLabel.implicitHeight + 12
                                radius: 6
                                color: "#62323d"
                                Text {
                                    id: lockLabel
                                    anchors.fill: parent
                                    anchors.margins: 6
                                    text: modelData
                                    wrapMode: Text.WordWrap
                                    color: "#ffbdc1"
                                    font.pixelSize: 11
                                }
                            }
                        }
                    }
                    Text {
                        Layout.fillWidth: true
                        text: backend.running ? "Arrow and packets show confirmed deliveries." : "Select an IPC mode and press Start to begin."
                        wrapMode: Text.WordWrap; color: win.muted; font.pixelSize: 11
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumWidth: 330
                radius: 14; color: win.panel
                border.color: "#29384f"
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 16; spacing: 12
                    Text { text: "CPU & Process Monitor"; font.pixelSize: 16; font.bold: true; color: "#f0f7ff" }
                    Text {
                        text: "Per-process CPU utilization · sampled about once per second"
                        font.pixelSize: 11; color: win.muted; wrapMode: Text.WordWrap; Layout.fillWidth: true
                    }
                    ScrollView {
                        id: metricsScroll
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        contentWidth: availableWidth
                        clip: true
                        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                        ScrollBar.vertical.policy: ScrollBar.AsNeeded

                        ColumnLayout {
                            width: metricsScroll.availableWidth
                            spacing: 9
                            Text {
                                visible: !backend.running
                                text: "Start the simulation to see worker readings."
                                color: win.muted; font.pixelSize: 11
                            }
                            Repeater {
                                model: backend.processMetrics.length
                                delegate: ColumnLayout {
                                    id: processRow
                                    required property int index
                                    objectName: "processMetric" + index
                                    readonly property var stats: win.metricsFor(index)
                                    readonly property bool hasCpu: !!stats && stats.cpu != null
                                    Layout.fillWidth: true
                                    spacing: 3
                                    RowLayout {
                                        Layout.fillWidth: true
                                        Text { text: processRow.stats ? "P" + processRow.stats.index : ""; color: "#e5f0fa"; font.pixelSize: 12; font.bold: true }
                                        Text { text: "PID: " + (processRow.stats ? processRow.stats.pid : "—"); color: win.muted; font.pixelSize: 11 }
                                        Item { Layout.fillWidth: true }
                                        Text {
                                            text: "CPU: " + (processRow.hasCpu ? Number(processRow.stats.cpu).toFixed(1) + "%" : "—")
                                            color: win.muted; font.pixelSize: 11
                                        }
                                    }
                                    Rectangle {
                                        Layout.fillWidth: true; height: 5; radius: 3; color: "#33465f"
                                        Rectangle {
                                            height: parent.height; radius: 3; color: win.cyan
                                            width: parent.width * (processRow.hasCpu ? Math.max(0, Math.min(100, processRow.stats.cpu)) : 0) / 100
                                            Behavior on width { NumberAnimation { duration: 350 } }
                                        }
                                    }
                                    RowLayout {
                                        Layout.fillWidth: true
                                        Text {
                                            text: "RSS: " + (processRow.stats && processRow.stats.memory != null
                                                            ? Number(processRow.stats.memory).toFixed(1) + " MiB" : "—")
                                            color: win.muted; font.pixelSize: 11
                                        }
                                        Item { Layout.fillWidth: true }
                                        Text {
                                            text: "OS: " + (processRow.stats ? (processRow.stats.status ?? "—") : "—")
                                            color: win.muted; font.pixelSize: 11
                                        }
                                        Text {
                                            text: processRow.stats ? (processRow.stats.alive ? "Alive" : "Exited") : "—"
                                            color: processRow.stats && processRow.stats.alive ? win.green : win.muted
                                            font.pixelSize: 11
                                        }
                                    }
                                }
                            }
                        }
                    }
                    Rectangle { Layout.fillWidth: true; height: 1; color: "#36485e" }
                    Text { text: "LIVE IPC METRICS"; font.pixelSize: 11; font.bold: true; color: win.cyan }
                    Text { text: backend.throughput; color: "#edf6ff"; font.pixelSize: 14 }
                    Text { text: backend.latency; color: "#edf6ff"; font.pixelSize: 14 }
                    Text { text: "Status: " + backend.status; color: win.muted; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 195
            radius: 14; color: win.panel
            border.color: "#29384f"
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 14; spacing: 9
                RowLayout {
                    Layout.fillWidth: true
                    Text { text: "Event Timeline"; color: "#f0f7ff"; font.pixelSize: 15; font.bold: true }
                    Item { Layout.fillWidth: true }
                    Text { text: "Throughput  ·  Latency  ·  Status"; color: win.muted; font.pixelSize: 11 }
                }
                ScrollView {
                    id: timelineScroll
                    objectName: "timelineScroll"
                    Layout.fillWidth: true; Layout.fillHeight: true
                    clip: true
                    background: Rectangle { color: "#0a1422"; radius: 9; border.color: "#22364c" }
                    TextArea {
                        id: timelineArea
                        objectName: "timelineArea"
                        readOnly: true
                        text: backend.timeline
                        color: "#8ce8ad"
                        font.family: "Consolas"
                        font.pixelSize: 12
                        wrapMode: TextEdit.WrapAnywhere
                        selectByMouse: true
                        padding: 12
                        background: null
                        HoverHandler { id: timelineHover }
                        onTextChanged: {
                            if (!timelineHover.hovered) cursorPosition = length
                        }
                    }
                }
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: visible ? alertText.implicitHeight + 14 : 0
                    visible: backend.alert !== ""
                    radius: 6; color: "#62323d"
                    Text {
                        id: alertText
                        objectName: "deadlockAlert"
                        anchors.fill: parent; anchors.margins: 7
                        text: "⚠ " + backend.alert
                        color: "#ffbdc1"; font.pixelSize: 12
                        wrapMode: Text.WordWrap
                    }
                }
            }
        }
    }
}
