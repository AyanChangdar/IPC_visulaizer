import QtQuick 2.15
import QtQuick.Controls 2.15
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

    readonly property color panel: "#192639"
    readonly property color card: "#202f44"
    readonly property color cyan: "#5bc7d8"
    readonly property color muted: "#a0b2ca"
    readonly property color green: "#72e5b0"
    property int transferSource: -1
    property int transferDestination: -1
    property real transferProgress: 0
    property int eventCounter: 0

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
        transferAnim.stop()
        transferSource = src
        transferDestination = dst
        transferProgress = 0
        eventCounter++
        transferAnim.start()
    }
    NumberAnimation {
        id: transferAnim
        target: win
        property: "transferProgress"
        from: 0; to: 1
        duration: 650
        easing.type: Easing.InOutQuad
        onStopped: flowCanvas.requestPaint()
    }
    Connections {
        target: backend
        function onDataFlow(src, dst) { win.fireTransfer(src, dst) }
        function onRunningChanged() { flowCanvas.requestPaint() }
        function onFrozenProcessesChanged() { flowCanvas.requestPaint() }
    }
    onTransferProgressChanged: flowCanvas.requestPaint()

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
                    from: 2; to: 20; value: 4
                    enabled: !backend.running
                    Layout.preferredWidth: 90
                    editable: true
                }
                Text { text: "IPC MODE"; color: win.muted; font.pixelSize: 10; font.bold: true }
                ComboBox {
                    id: ipcCombo
                    model: ["Pipe", "MsgQueue", "SharedMem"]
                    enabled: !backend.running
                    Layout.preferredWidth: 143
                    onCurrentIndexChanged: {
                        if (currentIndex !== 2 && backend.deadlockActive && !backend.running) backend.toggleDeadlock()
                    }
                }
                NexusButton {
                    text: "▶  Start"
                    fillColor: "#147d78"
                    enabled: !backend.running
                    onClicked: backend.start(procSpin.value, ipcCombo.currentIndex)
                }
                NexusButton {
                    text: "■  Stop"
                    fillColor: "#983f50"
                    enabled: backend.running
                    onClicked: backend.stop()
                }
                NexusButton {
                    text: backend.deadlockActive ? "Deadlock ON" : "Deadlock"
                    fillColor: backend.deadlockActive ? "#a44d36" : "#86613a"
                    enabled: !backend.running && ipcCombo.currentIndex === 2 && procSpin.value >= 4
                    onClicked: backend.toggleDeadlock()
                    ToolTip.visible: hovered
                    ToolTip.text: "Deadlock demo requires SharedMem and at least 4 processes; enable before Start"
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
                                var n = procSpin.value
                                var count = n === 4 ? 4 : n
                                ctx.lineWidth = 2.3
                                ctx.strokeStyle = backend.running ? "#4e9eaf" : "#375568"
                                for (var i = 0; i < count; i++) {
                                    var j = (i + 1) % count
                                    var sx = win.nodeX(i, count, width)
                                    var sy = win.nodeY(i, count, height)
                                    var ex = win.nodeX(j, count, width)
                                    var ey = win.nodeY(j, count, height)
                                    var dx = ex - sx, dy = ey - sy
                                    var len = Math.sqrt(dx*dx + dy*dy)
                                    if (len < 1) continue
                                    var ux = dx/len, uy = dy/len
                                    ctx.beginPath()
                                    ctx.moveTo(sx + ux*43, sy + uy*43)
                                    ctx.lineTo(ex - ux*43, ey - uy*43)
                                    ctx.stroke()
                                    var ax = ex - ux*43, ay = ey - uy*43
                                    ctx.beginPath()
                                    ctx.moveTo(ax, ay)
                                    ctx.lineTo(ax - ux*11 - uy*5, ay - uy*11 + ux*5)
                                    ctx.lineTo(ax - ux*11 + uy*5, ay - uy*11 - ux*5)
                                    ctx.closePath()
                                    ctx.fillStyle = ctx.strokeStyle
                                    ctx.fill()
                                }
                                if (win.transferSource >= 0 && win.transferDestination >= 0 && win.transferProgress < 1) {
                                    var x1 = win.nodeX(win.transferSource, n, width)
                                    var y1 = win.nodeY(win.transferSource, n, height)
                                    var x2 = win.nodeX(win.transferDestination, n, width)
                                    var y2 = win.nodeY(win.transferDestination, n, height)
                                    var px = x1 + (x2-x1)*win.transferProgress
                                    var py = y1 + (y2-y1)*win.transferProgress
                                    ctx.beginPath()
                                    ctx.arc(px, py, 8, 0, Math.PI*2)
                                    ctx.fillStyle = "#ffdc79"
                                    ctx.shadowColor = "#ffdc79"; ctx.shadowBlur = 18
                                    ctx.fill(); ctx.shadowBlur = 0
                                }
                            }
                        }
                        Repeater {
                            model: procSpin.value
                            delegate: Rectangle {
                                id: processNode
                                width: procSpin.value > 10 ? 62 : 84
                                height: procSpin.value > 10 ? 55 : 72
                                x: win.nodeX(index, procSpin.value, flowArea.width) - width/2
                                y: win.nodeY(index, procSpin.value, flowArea.height) - height/2
                                radius: 13
                                color: backend.frozenProcesses.indexOf(index) !== -1 ? "#603341" : "#273d53"
                                border.width: 2
                                border.color: backend.frozenProcesses.indexOf(index) !== -1 ? "#ff7e83" : win.cyan
                                Column {
                                    anchors.centerIn: parent
                                    spacing: 3
                                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: "P" + index; font.pixelSize: procSpin.value > 10 ? 16 : 22; font.bold: true; color: "#f5fbff" }
                                    Text {
                                        anchors.horizontalCenter: parent.horizontalCenter
                                        text: backend.frozenProcesses.indexOf(index) !== -1 ? "Waiting" : (backend.running ? "Running" : "Idle")
                                        color: backend.frozenProcesses.indexOf(index) !== -1 ? "#ff9e9e" : (backend.running ? win.green : win.muted)
                                        font.pixelSize: 10
                                    }
                                }
                            }
                        }
                    }
                    Text {
                        Layout.fillWidth: true
                        text: backend.running ? "Messages animate when the backend emits a data-flow event." : "Select an IPC mode and press Start to begin."
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
                    Text { text: backend.running ? "Live worker PID and CPU usage · updates every 0.5s" : "Start the simulation to see real process readings"; font.pixelSize: 11; color: win.muted; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    Repeater {
                        model: Math.min(procSpin.value, 8)
                        delegate: ColumnLayout {
                            id: processRow
                            Layout.fillWidth: true; spacing: 5
                            property var stats: index < backend.processStats.length ? backend.processStats[index] : null
                            property real cpuValue: stats && stats.cpu !== null && stats.cpu !== undefined ? Number(stats.cpu) : 0
                            RowLayout {
                                Layout.fillWidth: true
                                Text { text: "P" + index; color: "#e5f0fa"; font.pixelSize: 12; font.bold: true }
                                Text { text: "PID: " + (processRow.stats ? processRow.stats.pid : "—"); color: win.muted; font.pixelSize: 11 }
                                Item { Layout.fillWidth: true }
                                Text { text: "CPU: " + (processRow.stats && processRow.stats.cpu !== null && processRow.stats.cpu !== undefined ? Number(processRow.stats.cpu).toFixed(1) + "%" : "—"); color: win.muted; font.pixelSize: 11 }
                            }
                            Rectangle {
                                Layout.fillWidth: true; height: 5; radius: 3; color: "#33465f"
                                Rectangle {
                                    height: parent.height; radius: 3; color: win.cyan
                                    width: parent.width * Math.max(0, Math.min(100, cpuValue)) / 100
                                    Behavior on width { NumberAnimation { duration: 350 } }
                                }
                            }
                            Text { text: stats ? stats.state : "Idle"; color: win.muted; font.pixelSize: 10 }
                        }
                    }
                    Text {
                        visible: procSpin.value > 8
                        text: "+ " + (procSpin.value - 8) + " more processes (not shown)"
                        color: win.muted; font.pixelSize: 11
                    }
                    Rectangle { Layout.fillWidth: true; height: 1; color: "#36485e" }
                    Text { text: "LIVE IPC METRICS"; font.pixelSize: 11; font.bold: true; color: win.cyan }
                    Text { text: backend.throughput; color: "#edf6ff"; font.pixelSize: 14 }
                    Text { text: backend.latency; color: "#edf6ff"; font.pixelSize: 14 }
                    Text { text: "Status: " + backend.status; color: win.muted; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    Item { Layout.fillHeight: true }
                    Text {
                        Layout.fillWidth: true
                        text: "CPU usage is sampled from the operating system. Low values are normal for waiting processes."
                        color: "#8da3bc"; font.pixelSize: 11; wrapMode: Text.WordWrap
                    }
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
                    Layout.fillWidth: true; Layout.fillHeight: true
                    clip: true
                    background: Rectangle { color: "#0a1422"; radius: 9; border.color: "#22364c" }
                    TextArea {
                        id: timelineArea
                        readOnly: true
                        text: backend.timeline
                        color: "#8ce8ad"
                        font.family: "Consolas"
                        font.pixelSize: 12
                        wrapMode: TextEdit.WrapAnywhere
                        selectByMouse: true
                        padding: 12
                        background: null
                        onTextChanged: cursorPosition = length
                    }
                }
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: backend.alert !== "" ? 30 : 0
                    visible: backend.alert !== ""
                    radius: 6; color: "#62323d"
                    Text {
                        anchors.fill: parent; anchors.margins: 7
                        text: "⚠ " + backend.alert
                        color: "#ffbdc1"; font.pixelSize: 12
                        elide: Text.ElideRight
                    }
                }
            }
        }
    }
}
