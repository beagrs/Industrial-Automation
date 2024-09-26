import sys
import time
import signal

from PyQt5.QtWidgets import QApplication, QWidget, QSlider, QHBoxLayout, QVBoxLayout, QLabel, QMainWindow, QPushButton
from PyQt5.QtCore import Qt, QThread, QRunnable, pyqtSlot, QThreadPool, QObject, pyqtSignal, QRect
from PyQt5.QtGui import QPainter, QColor, QPen
from tango import AttributeProxy, DeviceProxy

# prefix for all Tango device names
TANGO_NAME_PREFIX = "epfl/station1"

# definition of Tango attribute and command names
TANGO_ATTRIBUTE_LEVEL = "level"
TANGO_ATTRIBUTE_VALVE = "valve"
TANGO_ATTRIBUTE_FLOW = "flow"
TANGO_ATTRIBUTE_COLOR = "color"
TANGO_COMMAND_FILL = "Fill"
TANGO_COMMAND_FLUSH = "Flush"


class TankWidget(QWidget):
    """
    Widget that displays the paint tank and valve
    """
    MARGIN_BOTTOM = 50
    VALVE_WIDTH = 15

    def __init__(self, tank_width, tank_height=200, level=0):
        super().__init__()
        self.fill_color = QColor("grey")
        self.fill_level = level
        self.tank_height = tank_height
        self.tank_width = tank_width
        self.valve = 0
        self.flow = 0
        self.setMinimumSize(self.tank_width, self.tank_height + self.MARGIN_BOTTOM)

    def setValve(self, valve):
        """
        set the valve level between 0 and 100
        """
        self.valve = valve

    def setFlow(self, flow):
        """
        set the value of the flow label
        """
        self.flow = flow

    def setColor(self, color):
        """
        set the color of the paint in hex format (e.g. #000000)
        """
        self.fill_color = QColor(color)

    def paintEvent(self, event):
        """
        paint method called to draw the UI elements
        """
        # get a painter object
        painter = QPainter(self)
        # draw tank outline as solid black line
        painter.setPen(QPen(Qt.black, 2, Qt.SolidLine))
        painter.drawRect(1, 1, self.width() - 2, self.height() - self.MARGIN_BOTTOM - 2)
        # draw paint color
        painter.setPen(QColor(0, 0, 0, 0))
        painter.setBrush(self.fill_color)
        painter.drawRect(2, 2 + int((1.0 - self.fill_level) * (self.height() - self.MARGIN_BOTTOM - 4)),
                         self.width() - 4,
                         int(self.fill_level * (self.height() - self.MARGIN_BOTTOM - 4)))
        # draw valve symobl
        painter.setPen(QPen(Qt.black, 2, Qt.SolidLine))
        painter.drawLine(self.width() // 2, self.height() - self.MARGIN_BOTTOM, self.width() // 2,
                         self.height() - self.MARGIN_BOTTOM + 5)
        painter.drawLine(self.width() // 2, self.height(), self.width() // 2,
                         self.height() - 5)
        painter.drawLine(self.width() // 2 - self.VALVE_WIDTH, self.height() - self.MARGIN_BOTTOM + 5,
                         self.width() // 2 + self.VALVE_WIDTH,
                         self.height() - 5)
        painter.drawLine(self.width() // 2 - self.VALVE_WIDTH, self.height() - 5, self.width() // 2 + self.VALVE_WIDTH,
                         self.height() - self.MARGIN_BOTTOM + 5)
        painter.drawLine(self.width() // 2 - self.VALVE_WIDTH, self.height() - self.MARGIN_BOTTOM + 5,
                         self.width() // 2 + self.VALVE_WIDTH,
                         self.height() - self.MARGIN_BOTTOM + 5)
        painter.drawLine(self.width() // 2 - self.VALVE_WIDTH, self.height() - 5, self.width() // 2 + self.VALVE_WIDTH,
                         self.height() - 5)
        # draw labels
        painter.drawText(
            QRect(0, self.height() - self.MARGIN_BOTTOM, self.width() // 2 - self.VALVE_WIDTH, self.MARGIN_BOTTOM),
            Qt.AlignCenter, "%u%%" % self.valve)
        painter.drawText(
            QRect(self.width() // 2 + self.VALVE_WIDTH, self.height() - self.MARGIN_BOTTOM,
                  self.width() // 2 - self.VALVE_WIDTH, self.MARGIN_BOTTOM),
            Qt.AlignCenter, "%.1f l/s" % self.flow)


class PaintTankWidget(QWidget):
    """
    Widget to hold a single paint tank, valve slider and command buttons
    """

    def __init__(self, name, width, fill_button=False, flush_button=False):
        super().__init__()
        self.name = name
        self.setGeometry(0, 0, width, 400)
        self.setMinimumSize(width, 400)
        self.layout = QVBoxLayout()
        self.threadpool = QThreadPool()
        self.worker = TangoBackgroundWorker(self.name)
        self.worker.level.done.connect(self.setLevel)
        self.worker.flow.done.connect(self.setFlow)
        self.worker.color.done.connect(self.setColor)
        self.worker.valve.done.connect(self.setValve)
        

        if fill_button:
            button = QPushButton('Fill', self)
            button.setToolTip('Fill up the tank with paint')
            button.clicked.connect(self.on_fill)
            self.layout.addWidget(button)

        # label for level
        self.label_level = QLabel("Level: --")
        self.label_level.setAlignment(Qt.AlignCenter)
        self.layout.addWidget(self.label_level)

        # tank widget
        self.tank = TankWidget(width)
        self.layout.addWidget(self.tank, 5)

        # slider for the valve
        self.slider = QSlider(Qt.Horizontal, self)
        self.slider.setFocusPolicy(Qt.NoFocus)
        self.slider.setGeometry(0, 0, width, 10)
        self.slider.setRange(0, 100)
        self.slider.setValue(0)  # valve closed
        self.slider.setSingleStep(10)
        self.slider.setTickInterval(20)
        self.timer_slider = None
        self.slider.valueChanged[int].connect(self.changedValue)
        self.layout.addWidget(self.slider)

        if flush_button:
            button = QPushButton('Flush', self)
            button.setToolTip('Flush the tank')
            button.clicked.connect(self.on_flush)
            self.layout.addWidget(button)

        self.setLayout(self.layout)

        # set the valve attribute to fully closed
        worker = TangoWriteAttributeWorker(self.name, TANGO_ATTRIBUTE_VALVE, self.slider.value() / 100.0)
        self.threadpool.start(worker)
        self.worker.start()
        # update the UI element
        self.tank.setValve(0)

    def changedValue(self):
        """
        callback when the value of the valve slider has changed
        """
        if self.timer_slider is not None:
            self.killTimer(self.timer_slider)
        # start a time that fires after 200 ms
        self.timer_slider = self.startTimer(200)

    def timerEvent(self, event):
        """
        callback when the timer has fired
        """
        self.killTimer(self.timer_slider)
        self.timer_slider = None

        # set valve attribute
        worker = TangoWriteAttributeWorker(self.name, TANGO_ATTRIBUTE_VALVE, self.slider.value() / 100.0)
        worker.signal.done.connect(self.setValve)
        self.threadpool.start(worker)

    def setLevel(self, level):
        """
        set the level of the paint tank, range: 0-1
        """
        self.tank.fill_level = level
        self.label_level.setText("Level: %.1f %%" % (level * 100))
        self.tank.update()

    def setValve(self, valve):
        """
        set the value of the valve label
        """
        if self.timer_slider is None and not self.slider.isSliderDown():
            # user is not currently changing the slider
            self.slider.setValue(int(valve*100))
            self.tank.setValve(valve*100)

    def setFlow(self, flow):
        """
        set the value of the flow label
        """
        self.tank.setFlow(flow)

    def setColor(self, color):
        """
        set the color of the paint
        """
        self.tank.setColor(color)

    def on_fill(self):
        """
        callback method for the "Fill" button
        """
        worker = TangoRunCommandWorker(self.name, TANGO_COMMAND_FILL)
        self.threadpool.start(worker)

    def on_flush(self):
        """
        callback method for the "Flush" button
        """
        worker = TangoRunCommandWorker(self.name, TANGO_COMMAND_FLUSH)
        self.threadpool.start(worker)


class ColorMixingPlantWindow(QMainWindow):
    """
    main UI window
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Color Mixing Plant Simulator - EPFL CS-487")
        self.setMinimumSize(900, 800)

        # Create a vertical layout
        vbox = QVBoxLayout()

        # Create a horizontal layout
        hbox = QHBoxLayout()

        self.window = QWidget()
        self.setCentralWidget(self.window)

        self.tanks = {"cyan": PaintTankWidget("cyan", width=150, fill_button=True),
                      "magenta": PaintTankWidget("magenta", width=150, fill_button=True),
                      "yellow": PaintTankWidget("yellow", width=150, fill_button=True),
                      "black": PaintTankWidget("black", width=150, fill_button=True),
                      "white": PaintTankWidget("white", width=150, fill_button=True),
                      "mixer": PaintTankWidget("mixer", width=860, flush_button=True)}

        hbox.addWidget(self.tanks["cyan"])
        hbox.addWidget(self.tanks["magenta"])
        hbox.addWidget(self.tanks["yellow"])
        hbox.addWidget(self.tanks["black"])
        hbox.addWidget(self.tanks["white"])

        vbox.addLayout(hbox)

        vbox.addWidget(self.tanks["mixer"])

        self.window.setLayout(vbox)


class WorkerSignal(QObject):
    """
    Implementation of a QT signal
    """
    done = pyqtSignal(object)


class TangoWriteAttributeWorker(QRunnable):
    """
    Worker class to write to a Tango attribute in the background.
    This is used to avoid blocking the main UI thread.
    """

    def __init__(self, device, attribute, value):
        super().__init__()
        self.signal = WorkerSignal()
        self.path = "%s/%s/%s" % (TANGO_NAME_PREFIX, device, attribute)
        self.value = value

    @pyqtSlot()
    def run(self):
        """
        main method of the worker
        """
        print("setDeviceAttribute: %s = %f" % (self.path, self.value))
        attr = AttributeProxy(self.path)
        try:
            # write attribute
            attr.write(self.value)
            # read back attribute
            data = attr.read()
            # send callback signal to UI
            self.signal.done.emit(data.value)
        except Exception as e:
            print("Failed to write to the Attribute: %s. Is the Device Server running?" % self.path)


class TangoRunCommandWorker(QRunnable):
    """
    Worker class to call a Tango command in the background.
    This is used to avoid blocking the main UI thread.
    """

    def __init__(self, device, command, *args):
        """
        creates a new instance for the given device instance and command
        :param device: device name
        :param command: name of the command
        :param args: command arguments
        """
        super().__init__()
        self.signal = WorkerSignal()
        self.device = "%s/%s" % (TANGO_NAME_PREFIX, device)
        self.command = command
        self.args = args

    @pyqtSlot()
    def run(self):
        """
        main method of the worker
        """
        print("device: %s command: %s args: %s" % (self.device, self.command, self.args))
        try:
            device = DeviceProxy(self.device)
            # get device server method
            func = getattr(device, self.command)
            # call command
            result = func(*self.args)
            # send callback signal to UI
            self.signal.done.emit(result)
        except Exception as e:
            print("Error calling device server command: device: %s command: %s" % (self.device, self.command))


class TangoBackgroundWorker(QThread):
    """
    This worker runs in the background and polls certain Tango device attributes (e.g. level, flow, color).
    It will signal to the UI when new data is available.
    """

    def __init__(self, name, interval=0.5):
        """
        creates a new instance
        :param name: device name
        :param interval: polling interval in seconds
        """
        super().__init__()
        self.name = name
        self.interval = interval
        self.level = WorkerSignal()
        self.flow = WorkerSignal()
        self.color = WorkerSignal()
        self.valve = WorkerSignal()

    def run(self):
        """
        main method of the worker
        """
        print("Starting TangoBackgroundWorker for '%s' tank" % self.name)
        # define attributes
        try:
            level = AttributeProxy("%s/%s/%s" % (TANGO_NAME_PREFIX, self.name, TANGO_ATTRIBUTE_LEVEL))
            flow = AttributeProxy("%s/%s/%s" % (TANGO_NAME_PREFIX, self.name, TANGO_ATTRIBUTE_FLOW))
            color = AttributeProxy("%s/%s/%s" % (TANGO_NAME_PREFIX, self.name, TANGO_ATTRIBUTE_COLOR))
            valve = AttributeProxy("%s/%s/%s" % (TANGO_NAME_PREFIX, self.name, TANGO_ATTRIBUTE_VALVE))
        except Exception as e:
            print("Error creating AttributeProxy for %s" % self.name)
            return

        while True:
            try:
                # read attributes
                data_color = color.read()
                data_level = level.read()
                data_flow = flow.read()
                data_valve = valve.read()
                # signal to UI
                self.color.done.emit(data_color.value)
                self.level.done.emit(data_level.value)
                self.flow.done.emit(data_flow.value)
                self.valve.done.emit(data_valve.value)
            except Exception as e:
                print("Error reading from the device: %s" % e)

            # wait for next round
            time.sleep(self.interval)


if __name__ == '__main__':
    # register signal handler for CTRL-C events
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    # init the QT application and the main window
    app = QApplication(sys.argv)
    ui = ColorMixingPlantWindow()
    # show the UI
    ui.show()
    # start the QT application (blocking until UI exits)
    app.exec_()
