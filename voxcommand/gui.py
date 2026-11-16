"""Graphical profile editor and listener control (PySide6)."""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import asdict
from importlib import resources
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QCompleter, QGroupBox, QScrollArea,
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QSlider, QSpinBox, QSplitter, QSystemTrayIcon, QTableWidget, QTextBrowser, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from .config import ACTION_TYPES, DEFAULT_PROFILE, Command, ProfileError, Settings, load_profile, save_profile
from .engine import App, listen

MODELS = ["tiny.en", "base.en", "small.en", "medium.en", "distil-large-v3", "large-v3-turbo",
          "tiny", "base", "small", "medium", "large-v3"]
# Settings the running engine only reads at start-up.
RESTART_KEYS = ("model", "device", "compute_type", "language", "microphone", "push_to_talk", "silence_ms")
ACTION_HINTS = {
    "key": "key or combo, e.g. g, f5, ctrl+shift+m",
    "press": "key to hold down until a matching release",
    "release": "key to let go",
    "type": "text to type; {text} = what a trailing * captured",
    "pause": "seconds, e.g. 0.5",
    "run": "shell command; {text} is inserted safely quoted",
    "mouse": "left, right, middle, back or forward",
    "scroll": "wheel steps, negative = down",
    "say": "text to speak; {time}, {date}, {day} = now",
    "sound": "path to a sound file",
    "listening": "on, off or toggle",
}
ACTION_GUIDE = """
<h2>Actions</h2>
<p>A command runs its actions top to bottom. <b>Hold</b> and <b>Times</b> are optional columns;
leave them blank for the defaults.</p>
<table border="1" cellpadding="5" cellspacing="0">
<tr><th>Action</th><th>Value</th><th>Hold (s)</th><th>Times</th><th>Example</th></tr>
<tr><td><b>key</b></td><td>A key or combo joined with <code>+</code>. Taps it (press, then release).</td>
  <td>How long to keep it down</td><td>Repeat count</td><td><code>g</code>, <code>f5</code>, <code>ctrl+shift+m</code></td></tr>
<tr><td><b>press</b></td><td>Key or combo to push down and <i>keep</i> down</td><td>-</td><td>-</td><td><code>w</code> (auto-run)</td></tr>
<tr><td><b>release</b></td><td>Key or combo to let go (pair with <b>press</b>)</td><td>-</td><td>-</td><td><code>w</code></td></tr>
<tr><td><b>type</b></td><td>Text to type. <code>{text}</code> = words captured by a trailing <code>*</code> phrase.
  US-layout characters only.</td><td>-</td><td>-</td><td><code>gg wp</code>, <code>{text}</code></td></tr>
<tr><td><b>mouse</b></td><td><code>left</code>, <code>right</code>, <code>middle</code>, <code>back</code>, <code>forward</code></td>
  <td>Hold the button</td><td>Clicks</td><td><code>right</code> + hold 0.2</td></tr>
<tr><td><b>scroll</b></td><td>Wheel steps; positive = up, negative = down</td><td>-</td><td>-</td><td><code>-3</code></td></tr>
<tr><td><b>pause</b></td><td>Seconds to wait (decimals allowed)</td><td>-</td><td>-</td><td><code>0.5</code></td></tr>
<tr><td><b>run</b></td><td>Shell command, started in the background. <code>{text}</code> is inserted safely quoted; <code>{query}</code> is the same text URL-encoded.
  Use <b>Run application…</b> to pick an installed app.</td><td>-</td><td>-</td><td><code>steam</code>, <code>xdg-open https://…</code></td></tr>
<tr><td><b>say</b></td><td>Text spoken aloud with the voice chosen in Settings. <code>{text}</code> allowed.
  To use a different voice for one command, add <code>voice: en_GB-alan-medium</code> (a downloaded Piper voice),
  a path to a <code>.onnx</code> voice file, or a system voice name, in the profile file.</td><td>-</td><td>-</td><td><code>shields up</code>, <code>It's {time}</code></td></tr>
<tr><td><b>sound</b></td><td>Path to an audio file to play</td><td>-</td><td>-</td><td><code>/home/you/sounds/ok.wav</code></td></tr>
<tr><td><b>listening</b></td><td><code>on</code>, <code>off</code> or <code>toggle</code>. While off, only commands with a
  listening action still work.</td><td>-</td><td>-</td><td><code>off</code></td></tr>
</table>
<p><b>Repeat</b> (below the table) runs the whole action list that many times.</p>

<h2>Key names</h2>
<table border="1" cellpadding="5" cellspacing="0">
<tr><td>Letters and digits</td><td><code>a</code> … <code>z</code>, <code>0</code> … <code>9</code></td></tr>
<tr><td>Function keys</td><td><code>f1</code> … <code>f24</code></td></tr>
<tr><td>Modifiers</td><td><code>ctrl</code> <code>shift</code> <code>alt</code> <code>super</code> (left side);
  <code>rctrl</code> <code>rshift</code> <code>ralt</code> <code>rightmeta</code></td></tr>
<tr><td>Editing</td><td><code>enter</code> <code>esc</code> <code>tab</code> <code>space</code> <code>backspace</code>
  <code>delete</code> <code>insert</code> <code>home</code> <code>end</code> <code>pgup</code> <code>pgdn</code> <code>caps</code></td></tr>
<tr><td>Arrows</td><td><code>up</code> <code>down</code> <code>left</code> <code>right</code></td></tr>
<tr><td>Number pad</td><td><code>kp0</code> … <code>kp9</code> <code>kpplus</code> <code>kpminus</code> <code>kpasterisk</code>
  <code>kpslash</code> <code>kpenter</code> <code>kpdot</code> <code>numlock</code></td></tr>
<tr><td>Symbols</td><td><code>-</code> <code>=</code> <code>,</code> <code>.</code> <code>/</code> <code>;</code> <code>'</code>
  <code>[</code> <code>]</code> <code>\\</code> <code>`</code> (or <code>minus</code> <code>comma</code> <code>tilde</code> …).
  A plus key is <code>plus</code>.</td></tr>
<tr><td>Media &amp; system</td><td><code>volumeup</code> <code>volumedown</code> <code>mute</code> <code>play</code>
  <code>next</code> <code>previous</code> <code>print</code> <code>scrolllock</code> <code>pause</code></td></tr>
<tr><td>Mouse buttons in combos</td><td><code>mouse_left</code> <code>mouse_right</code> <code>mouse_middle</code>
  <code>mouse4</code> <code>mouse5</code></td></tr>
<tr><td>Anything else</td><td>Any Linux key name, e.g. <code>KEY_SYSRQ</code>, <code>BTN_SIDE</code></td></tr>
</table>

<h2>Placeholders</h2>
<p>Usable in <b>say</b>, <b>type</b> and <b>run</b> values:</p>
<table border="1" cellpadding="5" cellspacing="0">
<tr><td><code>{text}</code></td><td>What you said after a phrase ending in <code>*</code></td></tr>
<tr><td><code>{query}</code></td><td>The same, URL-encoded (run only), for search URLs</td></tr>
<tr><td><code>{time}</code></td><td>Current time, e.g. <i>3:29 PM</i> or <i>4 o'clock PM</i></td></tr>
<tr><td><code>{time24}</code></td><td>24-hour time, e.g. <i>15:29</i></td></tr>
<tr><td><code>{date}</code></td><td>Today's date, e.g. <i>November 16, 2026</i></td></tr>
<tr><td><code>{day}</code></td><td>Weekday, e.g. <i>Monday</i></td></tr>
</table>
<p>Example: a command "what time is it" with the action <b>say</b> <code>It's {time}</code>.</p>

<h2>Phrases</h2>
<p><code>[a|b]</code> alternatives &nbsp; <code>[word|]</code> optional word &nbsp;
<code>type *</code> trailing star captures the rest of what you said as <code>{text}</code>.<br>
Example: <code>[open|show] [the|] map</code> matches "open map", "show the map" …</p>
"""

PHRASE_HELP = "One phrase per line.  [a|b] = alternatives   [word|] = optional word   trailing * = capture as {text}"


class EngineThread(QThread):
    log = Signal(str)
    level = Signal(float)
    ready = Signal()
    failed = Signal(str)

    def __init__(self, path: Path, dry_run: bool):
        super().__init__()
        self.path, self.dry_run = path, dry_run
        self.stop_event = threading.Event()
        self._peak, self._n = 0.0, 0

    def _level(self, rms: float):
        self._peak, self._n = max(self._peak, rms), self._n + 1
        if self._n >= 3:  # ~10 updates a second is plenty for a meter
            self.level.emit(self._peak)
            self._peak, self._n = 0.0, 0

    def run(self):
        app = None
        try:
            app = App(self.path, dry_run=self.dry_run, log=self.log.emit)
            listen(app, self.stop_event, self._level, self.ready.emit)
        except Exception as e:  # noqa: BLE001 - everything is reported in the window
            self.failed.emit(str(e))
        finally:
            if app is not None:
                app.executor.close()

    def stop(self):
        self.stop_event.set()


class GpuInstaller(QThread):
    progress = Signal(int, int, str)
    failed = Signal(str)

    def __init__(self):
        super().__init__()
        self.cancel = threading.Event()

    def run(self):
        from . import gpu

        try:
            gpu.install(lambda done, total, label: self.progress.emit(done // 1024, total // 1024, label),
                        self.cancel.is_set)
        except InterruptedError:
            pass
        except Exception as e:  # noqa: BLE001 - shown to the user
            self.failed.emit(str(e))


SEARCH_ENGINES = {
    "DuckDuckGo": ("duckduckgo", "https://duckduckgo.com/?q={query}"),
    "Google": ("google", "https://www.google.com/search?q={query}"),
    "Bing": ("bing", "https://www.bing.com/search?q={query}"),
    "Brave Search": ("brave", "https://search.brave.com/search?q={query}"),
    "Startpage": ("startpage", "https://www.startpage.com/do/search?query={query}"),
    "YouTube": ("youtube", "https://www.youtube.com/results?search_query={query}"),
    "Wikipedia": ("wikipedia", "https://en.wikipedia.org/w/index.php?search={query}"),
    "Reddit": ("reddit", "https://www.reddit.com/search/?q={query}"),
    "GitHub": ("github", "https://github.com/search?q={query}"),
    "Amazon": ("amazon", "https://www.amazon.com/s?k={query}"),
    "Steam store": ("steam", "https://store.steampowered.com/search/?term={query}"),
    "Fedora packages": ("fedora packages", "https://packages.fedoraproject.org/search?query={query}"),
    "Google Maps": ("maps", "https://www.google.com/maps/search/{query}"),
    "Custom URL…": ("", ""),
}


def quote_url(url: str) -> str:
    """Shell-quote a URL template around its {query} slots (which the run action fills in pre-quoted)."""
    import shlex

    return "{query}".join(shlex.quote(part) if part else "" for part in url.split("{query}"))


class WebSearchDialog(QDialog):
    """Build a "search <site> <words>" command that opens the results in the default browser."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add web search")
        self.resize(560, 260)
        form = QFormLayout(self)
        self.engine = QComboBox()
        self.engine.addItems(SEARCH_ENGINES)
        form.addRow("Search with", self.engine)
        self.url = QLineEdit()
        self.url.setToolTip("{query} is replaced by the words you say after the phrase")
        form.addRow("URL", self.url)
        self.phrase = QLineEdit()
        self.phrase.setToolTip("Say this, then your search words. [a|b] = alternatives, [word|] = optional")
        form.addRow("Phrase", self.phrase)
        self.example = QLabel()
        self.example.setEnabled(False)
        form.addRow("", self.example)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.ok = buttons.button(QDialogButtonBox.Ok)
        self.engine.currentTextChanged.connect(self._engine_changed)
        self.url.textChanged.connect(self._validate)
        self.phrase.textChanged.connect(self._validate)
        self._engine_changed(self.engine.currentText())

    def _engine_changed(self, name: str):
        word, url = SEARCH_ENGINES[name]
        self.url.setText(url or "https://example.com/search?q={query}")
        self.url.setReadOnly(bool(url))
        self.phrase.setText(f"[search|look up] {word or 'site'} [for|]")
        if not url:
            self.url.setFocus()
            self.url.selectAll()

    def _validate(self, *_):
        from .matcher import expand

        url, phrase = self.url.text().strip(), self.phrase.text().strip()
        ok = url.startswith(("http://", "https://")) and "{query}" in url and bool(phrase)
        self.ok.setEnabled(ok)
        if not ok:
            self.example.setText("The URL must start with http(s):// and contain {query}; the phrase can't be empty")
        else:
            self.example.setText(f'Say e.g. "{expand(phrase)[0]} linux voice control"')

    def command(self) -> dict:
        name = self.engine.currentText()
        label = name if SEARCH_ENGINES[name][1] else "web"
        return {
            "name": f"search {label}".lower(),
            "phrases": [self.phrase.text().strip().rstrip("*").strip() + " *"],
            "actions": [{"run": "xdg-open " + quote_url(self.url.text().strip())}],
            "repeat": 1,
        }


class VoiceDownloader(QThread):
    progress = Signal(int, int)
    failed = Signal(str)

    def __init__(self, key: str):
        super().__init__()
        self.key = key

    def run(self):
        from . import voices

        try:
            voices.download_voice(self.key, lambda done, total: self.progress.emit(done // 1024, total // 1024))
        except Exception as e:  # noqa: BLE001 - shown to the user
            self.failed.emit(str(e))


class VoiceShop(QDialog):
    """Browse the Piper voice catalogue; download or remove voices."""

    def __init__(self, parent=None, language: str | None = None):
        from . import voices

        super().__init__(parent)
        self.setWindowTitle("Get voices - VoxCommand")
        self.resize(640, 600)
        layout = QVBoxLayout(self)
        intro = QLabel("Natural-sounding Piper voices. They run offline once downloaded (about 60-120 MB each). "
                       "Double-click or press Download, then pick it in Settings and press Test voice.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.search = QLineEdit(placeholderText="Filter: language, country, name, quality…", clearButtonEnabled=True)
        layout.addWidget(self.search)
        self.list = QListWidget()
        layout.addWidget(self.list, 1)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        row = QHBoxLayout()
        self.download_button = QPushButton("Download")
        self.download_button.clicked.connect(self.download)
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self.remove)
        row.addWidget(self.download_button)
        row.addWidget(self.remove_button)
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)
        self.worker = None
        self.downloaded: list[str] = []
        try:
            self.catalog = voices.catalog()
        except Exception as e:  # noqa: BLE001
            self.catalog = {}
            intro.setText(f"Could not load the voice list (are you online?): {e}")
        self.search.textChanged.connect(self.fill)
        self.list.currentItemChanged.connect(lambda *_: self._buttons())
        self.list.itemDoubleClicked.connect(lambda *_: self.download())
        family = (language or "en").split("_")[0].lower()
        self.search.setText(next((i["language"]["name_english"] for i in self.catalog.values()
                                  if i["language"]["family"] == family), ""))

    def fill(self, text: str = ""):
        from . import voices

        installed = set(voices.installed_voices())
        words = text.lower().split()
        self.list.clear()
        for key, info in sorted(self.catalog.items(), key=lambda kv: (kv[1]["language"]["name_english"], kv[0])):
            label = voices.describe(key, info)
            if words and not all(w in (label + " " + key).lower() for w in words):
                continue
            size = sum(m["size_bytes"] for p, m in info["files"].items() if p.endswith(".onnx")) / 1e6
            item = QListWidgetItem(("✓ " if key in installed else "    ") + f"{label}  ({size:.0f} MB)")
            item.setData(Qt.UserRole, key)
            self.list.addItem(item)
        self.list.setCurrentRow(0)
        self._buttons()

    def _buttons(self):
        from . import voices

        item = self.list.currentItem()
        key = item.data(Qt.UserRole) if item else None
        busy = self.worker is not None
        installed = key in voices.installed_voices()
        self.download_button.setEnabled(bool(key) and not busy and not installed)
        self.remove_button.setEnabled(bool(key) and not busy and installed)

    def download(self):
        item = self.list.currentItem()
        if not item or self.worker is not None:
            return
        self.worker = VoiceDownloader(item.data(Qt.UserRole))
        self.worker.progress.connect(lambda done, total: (self.progress.setRange(0, total), self.progress.setValue(done)))
        self.worker.failed.connect(lambda msg: QMessageBox.warning(self, "VoxCommand", f"Download failed:\n{msg}"))
        self.worker.finished.connect(self._done)
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        self.worker.start()
        self._buttons()

    def _done(self):
        from . import voices

        if self.worker.key in voices.installed_voices():
            self.downloaded.append(self.worker.key)
        self.worker = None
        self.progress.setVisible(False)
        row = self.list.currentRow()
        self.fill(self.search.text())
        self.list.setCurrentRow(row)

    def remove(self):
        from . import voices

        item = self.list.currentItem()
        if item:
            voices.remove_voice(item.data(Qt.UserRole))
            row = self.list.currentRow()
            self.fill(self.search.text())
            self.list.setCurrentRow(row)

    def reject(self):
        if self.worker is None:
            super().reject()

    def accept(self):
        if self.worker is None:
            super().accept()


class ActionGuide(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Action guide - VoxCommand")
        self.resize(820, 680)
        layout = QVBoxLayout(self)
        view = QTextBrowser()
        view.setHtml(ACTION_GUIDE)
        layout.addWidget(view)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)


class AppPicker(QDialog):
    """Pick an installed application to launch with a `run` action."""

    def __init__(self, parent=None):
        from .apps import list_apps

        super().__init__(parent)
        self.setWindowTitle("Choose application")
        self.resize(460, 560)
        layout = QVBoxLayout(self)
        self.search = QLineEdit(placeholderText="Search applications…", clearButtonEnabled=True)
        layout.addWidget(self.search)
        self.list = QListWidget()
        for app in list_apps():
            item = QListWidgetItem(QIcon.fromTheme(app.icon) if app.icon else QIcon(), app.name)
            item.setData(Qt.UserRole, app)
            item.setToolTip(f"{app.comment}\n{app.command}".strip())
            self.list.addItem(item)
        self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(self.accept)
        layout.addWidget(self.list, 1)
        self.add_phrase = QCheckBox("Also add the phrase \"[open|launch|start] <name>\"")
        self.add_phrase.setChecked(True)
        layout.addWidget(self.add_phrase)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.search.textChanged.connect(self.filter)
        self.search.setFocus()

    def filter(self, text: str):
        text = text.lower()
        first = None
        for i in range(self.list.count()):
            item = self.list.item(i)
            app = item.data(Qt.UserRole)
            hidden = text not in f"{app.name} {app.comment} {app.command}".lower()
            item.setHidden(hidden)
            if not hidden and first is None:
                first = i
        if first is not None:
            self.list.setCurrentRow(first)

    def chosen(self):
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item and not item.isHidden() else None


class MainWindow(QMainWindow):
    def __init__(self, profile: Path):
        super().__init__()
        self.qsettings = QSettings("voxcommand", "voxcommand")
        self.icon = load_icon()
        self.setWindowIcon(self.icon)
        self.resize(980, 720)

        self.path = profile
        self.settings: dict = asdict(Settings())
        self.commands: list[dict] = []
        self.current = -1
        self.engine: EngineThread | None = None
        self.engine_settings: tuple | None = None
        self.restart_pending = False
        self.test_executor = None
        self._loading = False
        self.quitting = False
        self._shut_down = False
        self._system_voices = None

        self.save_timer = QTimer(self, singleShot=True, interval=700)
        self.save_timer.timeout.connect(self.save)

        self._build_ui()
        self._build_tray()
        self.load(profile)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        top = QHBoxLayout()
        self.path_label = QLabel()
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        top.addWidget(self.path_label, 1)
        for text, slot in (("Open…", self.open_dialog), ("New…", self.new_dialog), ("Save as…", self.save_as_dialog)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            top.addWidget(b)
        root.addLayout(top)

        bar = QHBoxLayout()
        self.start_button = QPushButton("Start listening")
        self.start_button.setCheckable(True)
        self.start_button.setMinimumHeight(36)
        self.start_button.clicked.connect(self.toggle_engine)
        bar.addWidget(self.start_button)
        self.dry_run = QCheckBox("Dry run (show actions, don't perform them)")
        bar.addWidget(self.dry_run)
        bar.addStretch(1)
        self.status = QLabel("Stopped")
        bar.addWidget(self.status)
        self.meter = QProgressBar()
        self.meter.setRange(0, 100)
        self.meter.setTextVisible(False)
        self.meter.setFixedWidth(160)
        self.meter.setToolTip("Microphone level")
        bar.addWidget(self.meter)
        root.addLayout(bar)

        split = QSplitter(Qt.Vertical)
        tabs = QTabWidget()
        tabs.addTab(self._commands_tab(), "Commands")
        tabs.addTab(self._settings_tab(), "Settings")
        split.addWidget(tabs)
        self.log_view = QPlainTextEdit(readOnly=True)
        self.log_view.setMaximumBlockCount(1000)
        self.log_view.setPlaceholderText("What VoxCommand hears and does appears here.")
        split.addWidget(self.log_view)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)

        quit_action = QAction("Quit", self)
        quit_action.setShortcut(QKeySequence.Quit)
        quit_action.triggered.connect(self.quit_app)
        self.addAction(quit_action)

        save = QAction("Save", self)
        save.setShortcut(QKeySequence.Save)
        save.triggered.connect(self.save)
        self.addAction(save)

    def _commands_tab(self):
        split = QSplitter()

        left = QWidget()
        lv = QVBoxLayout(left)
        self.search = QLineEdit(placeholderText="Filter commands…", clearButtonEnabled=True)
        self.search.textChanged.connect(self.apply_filter)
        lv.addWidget(self.search)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self.select)
        lv.addWidget(self.list, 1)
        row = QHBoxLayout()
        for text, slot in (("Add", self.add_command), ("Duplicate", self.duplicate_command), ("Delete", self.delete_command)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        lv.addLayout(row)
        search_button = QPushButton("Add web search…")
        search_button.setToolTip("Create a command that searches a site for what you say")
        search_button.clicked.connect(self.add_web_search)
        lv.addWidget(search_button)
        split.addWidget(left)

        self.editor = QWidget()
        ev = QVBoxLayout(self.editor)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.textEdited.connect(self.edited)
        form.addRow("Name", self.name_edit)
        ev.addLayout(form)
        ev.addWidget(QLabel("When I say"))
        self.phrases_edit = QPlainTextEdit()
        self.phrases_edit.setMaximumHeight(110)
        self.phrases_edit.textChanged.connect(self.edited)
        ev.addWidget(self.phrases_edit)
        hint = QLabel(PHRASE_HELP)
        hint.setWordWrap(True)
        hint.setEnabled(False)
        ev.addWidget(hint)

        ev.addWidget(QLabel("Do this"))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Action", "Value", "Hold (s)", "Times"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.itemChanged.connect(self.edited)
        ev.addWidget(self.table, 1)
        row = QHBoxLayout()
        for text, slot in (("Add action", self.add_action), ("Remove", self.remove_action),
                           ("Up", lambda: self.move_action(-1)), ("Down", lambda: self.move_action(1))):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        app_button = QPushButton("Run application…")
        app_button.setToolTip("Pick an installed application; sets the selected run action or adds one")
        app_button.clicked.connect(self.pick_application)
        row.addWidget(app_button)
        guide_button = QPushButton("Action guide")
        guide_button.setToolTip("All action types, their values and key names")
        guide_button.clicked.connect(self.show_guide)
        row.addWidget(guide_button)
        row.addStretch(1)
        row.addWidget(QLabel("Repeat"))
        self.repeat = QSpinBox(minimum=1, maximum=999)
        self.repeat.valueChanged.connect(self.edited)
        row.addWidget(self.repeat)
        self.test_button = QPushButton("Test in 3 s")
        self.test_button.setToolTip("Runs this command's actions after 3 seconds, so you can focus the target window first")
        self.test_button.clicked.connect(self.test_command)
        row.addWidget(self.test_button)
        ev.addLayout(row)
        split.addWidget(self.editor)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        return split

    def _settings_tab(self):
        w = QWidget()
        form = QFormLayout(w)
        self.model = QComboBox(editable=True)
        self.model.addItems(MODELS)
        self.model.setToolTip("Bigger models are more accurate but slower. '.en' models are English-only.")
        form.addRow("Whisper model", self.model)
        self.device = QComboBox()
        self.device.addItems(["auto", "cuda", "cpu"])
        form.addRow("Run on", self.device)
        self.language = QLineEdit(placeholderText="en, de, fr… (blank = detect; needs a non-.en model)")
        form.addRow("Language", self.language)
        self.microphone = QComboBox()
        form.addRow("Microphone", self.microphone)
        self.wake_word = QLineEdit(placeholderText='e.g. "computer" (blank = none)')
        form.addRow("Wake word", self.wake_word)
        self.push_to_talk = QLineEdit(placeholderText="e.g. rightctrl, f13, mouse4 (blank = always listening)")
        form.addRow("Push-to-talk key", self.push_to_talk)
        self.threshold = QSpinBox(minimum=50, maximum=100, suffix=" %")
        self.threshold.setToolTip("Raise if wrong commands fire; lower if commands are missed")
        form.addRow("Match strictness", self.threshold)
        self.key_press = QSpinBox(minimum=10, maximum=1000, suffix=" ms")
        self.key_press.setToolTip("How long tapped keys stay down; some games need 80-100 ms")
        form.addRow("Key press length", self.key_press)
        self.silence = QSpinBox(minimum=150, maximum=3000, singleStep=50, suffix=" ms")
        self.silence.setToolTip("Pause that ends a command; lower is snappier but may cut you off")
        form.addRow("End-of-speech pause", self.silence)

        voice_box = QGroupBox("Spoken replies (say actions)")
        vform = QFormLayout(voice_box)
        self.voice_engine = QComboBox()
        self.voice_engine.addItem("System voice (speech-dispatcher / espeak-ng)", "system")
        self.voice_engine.addItem("Natural voice (Piper, offline)", "piper")
        vform.addRow("Engine", self.voice_engine)
        vrow = QHBoxLayout()
        self.voice = QComboBox(editable=True)
        self.voice.setInsertPolicy(QComboBox.NoInsert)
        self.voice.setMinimumContentsLength(30)
        self.voice.setToolTip("Type to search. System voices are Language+Variant, e.g. English (America)+Andrea")
        vrow.addWidget(self.voice, 1)
        self.voice_shop_button = QPushButton("Get voices…")
        self.voice_shop_button.clicked.connect(self.open_voice_shop)
        vrow.addWidget(self.voice_shop_button)
        self.voice_file_button = QPushButton("Voice file…")
        self.voice_file_button.setToolTip("Use your own Piper voice (.onnx with its .onnx.json next to it)")
        self.voice_file_button.clicked.connect(self.choose_voice_file)
        vrow.addWidget(self.voice_file_button)
        vform.addRow("Voice", vrow)
        self.voice_rate, self.voice_pitch, self.voice_volume = (self._slider() for _ in range(3))
        vform.addRow("Speed", self.voice_rate)
        self.pitch_label = QLabel("Pitch")
        vform.addRow(self.pitch_label, self.voice_pitch)
        vform.addRow("Volume", self.voice_volume)
        trow = QHBoxLayout()
        self.voice_test_text = QLineEdit("Hello, I'm your VoxCommand voice. It's {time}.")
        trow.addWidget(self.voice_test_text, 1)
        self.voice_test_button = QPushButton("Test voice")
        self.voice_test_button.clicked.connect(self.test_voice)
        trow.addWidget(self.voice_test_button)
        vform.addRow("Try it", trow)
        form.addRow(voice_box)
        self.voice_engine.currentIndexChanged.connect(self._voice_engine_changed)
        self.voice.currentTextChanged.connect(self.edited)
        for slider in (self.voice_rate, self.voice_pitch, self.voice_volume):
            slider.valueChanged.connect(self.edited)

        gpu_row = QHBoxLayout()
        self.gpu_status = QLabel()
        self.gpu_status.setWordWrap(True)
        gpu_row.addWidget(self.gpu_status, 1)
        self.gpu_progress = QProgressBar()
        self.gpu_progress.setVisible(False)
        gpu_row.addWidget(self.gpu_progress, 1)
        self.gpu_button = QPushButton()
        self.gpu_button.clicked.connect(self.gpu_clicked)
        gpu_row.addWidget(self.gpu_button)
        form.addRow("GPU acceleration", gpu_row)

        from . import autostart

        start_row = QHBoxLayout()
        self.autostart = QCheckBox("Start VoxCommand when I log in")
        self.autostart.setToolTip(f"Adds {autostart.entry_path()}; it starts in the system tray")
        self.autostart_listen = QCheckBox("and start listening")
        self.autostart.setChecked(autostart.is_enabled())
        self.autostart_listen.setChecked(autostart.listens_on_start() or not autostart.is_enabled())
        self.autostart_listen.setEnabled(self.autostart.isChecked())
        self.autostart.toggled.connect(self.autostart_changed)
        self.autostart_listen.toggled.connect(self.autostart_changed)
        start_row.addWidget(self.autostart)
        start_row.addWidget(self.autostart_listen)
        start_row.addStretch(1)
        form.addRow("Startup", start_row)
        self.gpu_installer = None
        self._update_gpu_ui()

        for combo in (self.model, self.device, self.microphone):
            combo.currentTextChanged.connect(self.edited)
        for line in (self.language, self.wake_word, self.push_to_talk):
            line.textEdited.connect(self.edited)
        for spin in (self.threshold, self.key_press, self.silence):
            spin.valueChanged.connect(self.edited)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(w)
        return scroll

    def _build_tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self.icon, self)
        menu = QMenu()
        menu.addAction("Show VoxCommand", self.show_window)
        self.tray_toggle = menu.addAction("Start listening", self.toggle_engine)
        menu.addSeparator()
        menu.addAction("Quit", self.quit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_window() if reason == QSystemTrayIcon.Trigger else None)
        self.tray.show()

    def show_window(self):
        self.show()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------- profile
    def load(self, path: Path):
        try:
            profile = load_profile(path)
        except (OSError, ProfileError) as e:
            QMessageBox.warning(self, "VoxCommand", f"Could not open profile:\n{e}")
            return False
        self.save_timer.stop()
        self.path = Path(path)
        self.qsettings.setValue("profile", str(self.path))
        self.settings = asdict(profile.settings)
        self.commands = [
            {"name": c.name, "phrases": list(c.phrases), "actions": [dict(a) for a in c.actions], "repeat": c.repeat}
            for c in profile.commands
        ]
        self._loading = True
        self.path_label.setText(str(self.path))
        self.setWindowTitle(f"{self.path.stem} - VoxCommand")
        self._load_settings()
        self.current = -1
        self.list.clear()
        self.list.addItems([c["name"] for c in self.commands])
        self._loading = False
        self.list.setCurrentRow(0 if self.commands else -1)
        self.select(self.list.currentRow())
        return True

    def _load_settings(self):
        s = self.settings
        self.model.setCurrentText(str(s["model"]))
        self.device.setCurrentText(str(s["device"]))
        self.language.setText(s["language"] or "")
        self.microphone.clear()
        self.microphone.addItem("System default", None)
        try:
            from .audio import sounddevice

            for dev in sounddevice().query_devices():
                if dev["max_input_channels"] > 0:
                    self.microphone.addItem(dev["name"], dev["name"])
        except Exception as e:  # noqa: BLE001 - no PortAudio; listing is only a convenience
            self.append_log(f"! could not list microphones: {e}")
        if s["microphone"] is not None:
            i = self.microphone.findData(s["microphone"])
            if i < 0:  # index or name fragment from a hand-written profile: keep it as is
                self.microphone.addItem(str(s["microphone"]), s["microphone"])
                i = self.microphone.count() - 1
            self.microphone.setCurrentIndex(i)
        self.wake_word.setText(s["wake_word"] or "")
        self.push_to_talk.setText(s["push_to_talk"] or "")
        self.threshold.setValue(int(s["match_threshold"]))
        self.key_press.setValue(int(s["key_press_ms"]))
        self.silence.setValue(int(s["silence_ms"]))
        self.voice_engine.setCurrentIndex(max(0, self.voice_engine.findData(s["voice_engine"])))
        self._fill_voices(s["voice"])
        self.voice_rate.setValue(int(s["voice_rate"]))
        self.voice_pitch.setValue(int(s["voice_pitch"]))
        self.voice_volume.setValue(int(s["voice_volume"]))

    def _collect_settings(self):
        self.settings.update(
            model=self.model.currentText().strip() or "small.en",
            device=self.device.currentText(),
            language=self.language.text().strip() or None,
            microphone=self.microphone.currentData(),
            wake_word=self.wake_word.text().strip() or None,
            push_to_talk=self.push_to_talk.text().strip() or None,
            match_threshold=self.threshold.value(),
            key_press_ms=self.key_press.value(),
            silence_ms=self.silence.value(),
            voice_engine=self.voice_engine.currentData(),
            voice=self._voice_value(),
            voice_rate=self.voice_rate.value(),
            voice_pitch=self.voice_pitch.value(),
            voice_volume=self.voice_volume.value(),
        )

    def edited(self, *_):
        """Any widget changed: pull the editor into the model and schedule an autosave."""
        if self._loading:
            return
        self._commit_command()
        self.save_timer.start()

    def save(self):
        if self._loading:
            return
        self.save_timer.stop()
        self._commit_command()
        self._collect_settings()
        try:
            save_profile(self.path, self.settings, self.commands)
        except OSError as e:
            self.append_log(f"! could not save {self.path}: {e}")
            return
        if self.engine and self.engine_settings != self._restart_snapshot():
            self.append_log("settings changed, restarting the listener")
            self.restart_pending = True
            self.engine.stop()

    def _restart_snapshot(self):
        return tuple(self.settings[k] for k in RESTART_KEYS)

    def open_dialog(self):
        name, _ = QFileDialog.getOpenFileName(self, "Open profile", str(self.path.parent), "Profiles (*.yaml *.yml)")
        if name:
            self.save()
            was_running = self.engine is not None
            if self.load(Path(name)) and was_running:
                self.restart_pending = True
                self.engine.stop()

    def new_dialog(self):
        name, _ = QFileDialog.getSaveFileName(self, "New profile", str(self.path.parent / "new-profile.yaml"), "Profiles (*.yaml)")
        if name:
            self.save()
            save_profile(name, Settings(**self.settings), [])
            self.load(Path(name))

    def save_as_dialog(self):
        name, _ = QFileDialog.getSaveFileName(self, "Save profile as", str(self.path), "Profiles (*.yaml)")
        if name:
            self.path = Path(name)
            self.save()
            self.load(self.path)

    # ------------------------------------------------------------ commands
    def select(self, row: int):
        if self._loading:
            return
        self._commit_command()
        self.current = row
        self.editor.setEnabled(0 <= row < len(self.commands))
        self._loading = True
        self.table.setRowCount(0)
        if self.editor.isEnabled():
            c = self.commands[row]
            self.name_edit.setText(c["name"])
            self.phrases_edit.setPlainText("\n".join(c["phrases"]))
            self.repeat.setValue(c.get("repeat", 1))
            for action in c["actions"]:
                self._append_action_row(action)
        else:
            self.name_edit.clear()
            self.phrases_edit.clear()
        self._loading = False

    def _append_action_row(self, action: dict, at: int | None = None):
        row = self.table.rowCount() if at is None else at
        self.table.insertRow(row)
        kind = next((t for t in ACTION_TYPES if t in action), "key")
        combo = QComboBox()
        combo.addItems(ACTION_TYPES)
        combo.setCurrentText(kind)
        # Keys this table has no column for (e.g. run's `wait`) ride along untouched.
        combo.setProperty("extra", {k: v for k, v in action.items() if k not in (kind, "hold", "times")})
        combo.currentTextChanged.connect(lambda _=None, c=combo: self._kind_changed(c))
        self.table.setCellWidget(row, 0, combo)
        value = action.get(kind, "")
        if kind == "listening" and isinstance(value, bool):
            value = "on" if value else "off"
        item = QTableWidgetItem(str(value))
        item.setToolTip(ACTION_HINTS[kind])
        self.table.setItem(row, 1, item)
        self.table.setItem(row, 2, QTableWidgetItem("" if action.get("hold") is None else str(action["hold"])))
        self.table.setItem(row, 3, QTableWidgetItem("" if action.get("times") is None else str(action["times"])))

    def _kind_changed(self, combo: QComboBox):
        for row in range(self.table.rowCount()):
            if self.table.cellWidget(row, 0) is combo and self.table.item(row, 1):
                self.table.item(row, 1).setToolTip(ACTION_HINTS[combo.currentText()])
        self.edited()

    def _row_action(self, row: int) -> dict:
        def cell(col):
            item = self.table.item(row, col)
            return item.text().strip() if item else ""

        def number(text, kind):
            try:
                return kind(text)
            except ValueError:
                return text  # left as typed; the listener reports it when the command runs

        combo = self.table.cellWidget(row, 0)
        kind = combo.currentText()
        value = cell(1) if kind != "type" else (self.table.item(row, 1).text() if self.table.item(row, 1) else "")
        if kind == "pause":
            value = number(value, float)
        elif kind == "scroll":
            value = number(value, int)
        action = {kind: value}
        if cell(2):
            action["hold"] = number(cell(2), float)
        if cell(3):
            action["times"] = number(cell(3), int)
        action.update(combo.property("extra") or {})
        return action

    def _commit_command(self):
        if self._loading or not (0 <= self.current < len(self.commands)):
            return
        c = self.commands[self.current]
        phrases = [p.strip() for p in self.phrases_edit.toPlainText().splitlines() if p.strip()]
        c["phrases"] = phrases or ["new command"]
        c["name"] = self.name_edit.text().strip() or c["phrases"][0]
        c["repeat"] = self.repeat.value()
        c["actions"] = [self._row_action(r) for r in range(self.table.rowCount())]
        item = self.list.item(self.current)
        if item and item.text() != c["name"]:
            item.setText(c["name"])

    def apply_filter(self, text: str):
        text = text.lower()
        for i, c in enumerate(self.commands):
            hay = (c["name"] + " " + " ".join(c["phrases"])).lower()
            self.list.item(i).setHidden(text not in hay)

    def add_command(self, _=False, template: dict | None = None):
        self._commit_command()
        self.search.clear()
        c = template or {"name": "new command", "phrases": ["new command"], "actions": [{"key": ""}], "repeat": 1}
        self.commands.append(c)
        self.list.addItem(c["name"])
        self.list.setCurrentRow(len(self.commands) - 1)
        self.name_edit.setFocus()
        self.name_edit.selectAll()
        self.save_timer.start()

    def duplicate_command(self):
        if 0 <= self.current < len(self.commands):
            self._commit_command()
            src = self.commands[self.current]
            self.add_command(template={
                "name": src["name"] + " copy", "phrases": list(src["phrases"]),
                "actions": [dict(a) for a in src["actions"]], "repeat": src.get("repeat", 1),
            })

    def delete_command(self):
        row = self.current
        if not (0 <= row < len(self.commands)):
            return
        name = self.commands[row]["name"]
        if QMessageBox.question(self, "VoxCommand", f'Delete the command "{name}"?') != QMessageBox.Yes:
            return
        self._loading = True
        del self.commands[row]
        self.list.takeItem(row)
        self.current = -1
        self._loading = False
        self.list.setCurrentRow(min(row, len(self.commands) - 1))
        self.select(self.list.currentRow())
        self.save_timer.start()

    def add_action(self):
        self._loading = True
        self._append_action_row({"key": ""})
        self._loading = False
        self.table.setCurrentCell(self.table.rowCount() - 1, 1)
        self.table.editItem(self.table.item(self.table.rowCount() - 1, 1))

    def remove_action(self):
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)
            self.edited()

    def move_action(self, delta: int):
        row = self.table.currentRow()
        target = row + delta
        if row < 0 or not (0 <= target < self.table.rowCount()):
            return
        action = self._row_action(row)
        self._loading = True
        self.table.removeRow(row)
        self._append_action_row(action, at=target)
        self._loading = False
        self.table.setCurrentCell(target, 1)
        self.edited()

    def show_guide(self):
        # Non-modal so it can stay open beside the editor.
        if getattr(self, "guide", None) is None:
            self.guide = ActionGuide(self)
        self.guide.show()
        self.guide.raise_()

    def add_web_search(self):
        dialog = WebSearchDialog(self)
        if dialog.exec() == QDialog.Accepted:
            self.add_command(template=dialog.command())
            self.append_log(f'added "{self.commands[-1]["name"]}"')

    def pick_application(self):
        if not (0 <= self.current < len(self.commands)):
            return
        dialog = AppPicker(self)
        if dialog.exec() != QDialog.Accepted or not (app := dialog.chosen()):
            return
        from .apps import suggested_phrase

        row = self.table.currentRow()
        combo = self.table.cellWidget(row, 0) if row >= 0 else None
        self._loading = True
        if combo is not None and combo.currentText() == "run":
            self.table.item(row, 1).setText(app.command)
        else:
            row = 0 if self._is_blank_command() else self.table.rowCount()
            if row == 0:
                self.table.setRowCount(0)
            self._append_action_row({"run": app.command})
        if dialog.add_phrase.isChecked():
            phrases = [p for p in self.phrases_edit.toPlainText().splitlines() if p.strip()]
            if phrases == ["new command"]:
                phrases = []
            if suggested_phrase(app) not in phrases:
                phrases.append(suggested_phrase(app))
            self.phrases_edit.setPlainText("\n".join(phrases))
            if self.name_edit.text().strip() in ("", "new command"):
                self.name_edit.setText(f"open {app.name}")
        self._loading = False
        self.table.setCurrentCell(row, 1)
        self.edited()

    def _is_blank_command(self) -> bool:
        """A freshly added command still holding its single empty placeholder action."""
        if self.table.rowCount() != 1:
            return False
        item = self.table.item(0, 1)
        return not (item and item.text().strip())

    def test_command(self):
        if not (0 <= self.current < len(self.commands)):
            return
        self._commit_command()
        c = self.commands[self.current]
        command = Command(phrases=c["phrases"], actions=[dict(a) for a in c["actions"]], name=c["name"], repeat=c["repeat"])
        self.append_log(f'testing "{c["name"]}" in 3 seconds - switch to the target window')
        QTimer.singleShot(3000, lambda: self._run_test(command))

    def _run_test(self, command: Command):
        from .actions import Executor

        if self.test_executor is None:
            self.test_executor = Executor(log=lambda msg: QTimer.singleShot(0, self, lambda: self.append_log(msg)))
        self.test_executor.dry_run = self.dry_run.isChecked()
        self.test_executor.key_press_s = self.key_press.value() / 1000
        self.test_executor.submit(command)

    def autostart_changed(self, *_):
        """Kept outside the profile: it belongs to this computer, not to a command set."""
        from . import autostart

        on = self.autostart.isChecked()
        self.autostart_listen.setEnabled(on)
        try:
            if on:
                autostart.enable(listen=self.autostart_listen.isChecked())
            else:
                autostart.disable()
        except OSError as e:
            self.append_log(f"! could not change autostart: {e}")
            return
        self.append_log("will start at login" if on else "will not start at login")

    # --------------------------------------------------------------- voice
    def _slider(self):
        slider = QSlider(Qt.Horizontal, minimum=-100, maximum=100, singleStep=5, pageStep=20)
        slider.setTickPosition(QSlider.TicksBelow)
        slider.setTickInterval(50)
        slider.setToolTip("Middle = normal")
        return slider

    def _fill_voices(self, current: str | None):
        """Fill the voice list for the chosen engine; values are stored as item data."""
        from . import voices

        piper = self.voice_engine.currentData() == "piper"
        was_loading, self._loading = self._loading, True
        self.voice.clear()
        self.voice.addItem("Default", None)
        if piper:
            try:
                cat = voices.catalog()
            except Exception:  # noqa: BLE001 - offline and no cache: show bare names
                cat = {}
            for key in voices.installed_voices():
                self.voice.addItem(voices.describe(key, cat[key]) if key in cat else key, key)
        else:
            if self._system_voices is None:
                self._system_voices = voices.system_voices()
            for name in self._system_voices:
                self.voice.addItem(name, name)
        if current:
            i = self.voice.findData(current)
            if i < 0:  # a voice file path, or a name not in the list
                self.voice.addItem(Path(current).name if current.endswith(".onnx") else current, current)
                i = self.voice.count() - 1
            self.voice.setCurrentIndex(i)
        completer = QCompleter(self.voice.model(), self.voice)
        completer.setFilterMode(Qt.MatchContains)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.voice.setCompleter(completer)
        self.voice_shop_button.setVisible(piper)
        self.voice_file_button.setVisible(piper)
        self.voice_pitch.setEnabled(not piper)
        self.pitch_label.setEnabled(not piper)
        self._loading = was_loading

    def _voice_engine_changed(self, *_):
        if self._loading:
            return
        self._fill_voices(None)
        self.edited()

    def _voice_value(self):
        i = self.voice.currentIndex()
        if i >= 0 and self.voice.itemText(i) == self.voice.currentText():
            return self.voice.itemData(i)
        return self.voice.currentText().strip() or None  # typed by hand

    def open_voice_shop(self):
        dialog = VoiceShop(self, self.language.text().strip() or "en")
        dialog.exec()
        current = self._voice_value()
        self._fill_voices(dialog.downloaded[-1] if dialog.downloaded and not current else current)
        self.edited()

    def choose_voice_file(self):
        name, _ = QFileDialog.getOpenFileName(self, "Piper voice file", str(Path.home()), "Piper voices (*.onnx)")
        if not name:
            return
        if not Path(name + ".json").exists():
            QMessageBox.warning(self, "VoxCommand", f"{Path(name).name}.json must be next to the voice file.")
            return
        self._fill_voices(name)
        self.edited()

    def test_voice(self):
        from .actions import fill_clock
        from .voices import VoiceSettings, say

        settings = VoiceSettings(self.voice_engine.currentData(), self._voice_value(), self.voice_rate.value(),
                                 self.voice_pitch.value(), self.voice_volume.value())
        text = fill_clock(self.voice_test_text.text()) or "Hello"
        self.voice_test_button.setEnabled(False)

        def run():
            try:
                say(text, settings)
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                QTimer.singleShot(0, self, lambda: self.append_log(f"! voice: {msg}"))
            finally:
                QTimer.singleShot(0, self, lambda: self.voice_test_button.setEnabled(True))

        threading.Thread(target=run, daemon=True).start()

    # ----------------------------------------------------------------- gpu
    def _update_gpu_ui(self):
        from . import gpu

        busy = self.gpu_installer is not None
        self.gpu_progress.setVisible(busy)
        if busy:
            self.gpu_button.setText("Cancel")
        elif gpu.is_installed():
            self.gpu_status.setText("NVIDIA libraries installed")
            self.gpu_button.setText("Remove")
        else:
            bundled = _bundled_cuda()
            driver = "" if gpu.has_nvidia_driver() else " (no NVIDIA driver detected)"
            self.gpu_status.setText("Included in this build" if bundled else
                                    f"Not installed: Whisper runs on the CPU{driver}")
            self.gpu_button.setText("Download (1.4 GB)")
            self.gpu_button.setEnabled(not bundled)

    def gpu_clicked(self):
        from . import gpu

        if self.gpu_installer is not None:
            self.gpu_installer.cancel.set()
            self.gpu_button.setEnabled(False)
            return
        if gpu.is_installed():
            if QMessageBox.question(self, "VoxCommand", "Remove the downloaded NVIDIA libraries?") == QMessageBox.Yes:
                gpu.uninstall()
                self.append_log("GPU libraries removed; restart VoxCommand to switch to the CPU")
            self._update_gpu_ui()
            return
        if not gpu.has_nvidia_driver() and QMessageBox.question(
            self, "VoxCommand", "No NVIDIA driver was detected, so the GPU libraries may not work. Download anyway?"
        ) != QMessageBox.Yes:
            return
        self.gpu_installer = GpuInstaller()
        self.gpu_installer.progress.connect(self._gpu_progress)
        self.gpu_installer.failed.connect(lambda msg: QMessageBox.warning(self, "VoxCommand", f"GPU download failed:\n{msg}"))
        self.gpu_installer.finished.connect(self._gpu_finished)
        self.gpu_progress.setRange(0, 0)
        self.gpu_status.setText("Contacting PyPI…")
        self.gpu_installer.start()
        self._update_gpu_ui()

    def _gpu_progress(self, done_kb: int, total_kb: int, label: str):
        self.gpu_progress.setRange(0, total_kb)
        self.gpu_progress.setValue(done_kb)
        self.gpu_status.setText(f"{label} ({done_kb / 1e6:.2f} / {total_kb / 1e6:.2f} GB)")

    def _gpu_finished(self):
        from . import gpu

        self.gpu_installer = None
        self.gpu_button.setEnabled(True)
        self._update_gpu_ui()
        if gpu.is_installed():
            self.append_log("GPU libraries installed")
            if self.engine:
                self.append_log("restarting the listener to use the GPU")
                self.restart_pending = True
                self.engine.stop()

    # -------------------------------------------------------------- engine
    def toggle_engine(self):
        if self.engine:
            self.restart_pending = False
            self.status.setText("Stopping…")
            self.engine.stop()
        else:
            self.start_engine()

    def start_engine(self):
        self.save()
        self.engine_settings = self._restart_snapshot()
        self.engine = EngineThread(self.path, self.dry_run.isChecked())
        self.engine.log.connect(self.append_log)
        self.engine.level.connect(lambda rms: self.meter.setValue(min(100, int(rms * 600))))
        self.engine.ready.connect(lambda: self._set_running_ui("Listening"))
        self.engine.failed.connect(self._engine_failed)
        self.engine.finished.connect(self._engine_finished)
        self._set_running_ui("Loading model…")
        self.dry_run.setEnabled(False)
        self.engine.start()

    def _set_running_ui(self, status: str):
        running = self.engine is not None
        self.status.setText(status)
        self.start_button.setChecked(running)
        self.start_button.setText("Stop listening" if running else "Start listening")
        if self.tray:
            self.tray_toggle.setText(self.start_button.text())
            self.tray.setToolTip(f"VoxCommand - {status}")

    def _engine_failed(self, message: str):
        self.restart_pending = False
        self.append_log(f"! {message}")
        QMessageBox.warning(self, "VoxCommand", message)

    def _engine_finished(self):
        self.engine = None
        self.meter.setValue(0)
        self.dry_run.setEnabled(True)
        self._set_running_ui("Stopped")
        if self.restart_pending:
            self.restart_pending = False
            self.start_engine()

    def append_log(self, message: str):
        self.log_view.appendPlainText(f"{time.strftime('%H:%M:%S')} {message}")

    def closeEvent(self, event):
        # With a tray icon, closing the window only hides it; Quit (tray menu or Ctrl+Q) exits.
        if self.tray is not None and not self.quitting:
            self.save()
            self.hide()
            event.ignore()
            if not self.qsettings.value("tray_hint_shown", False, type=bool):
                self.qsettings.setValue("tray_hint_shown", True)
                self.tray.showMessage(
                    "VoxCommand", "Still running in the system tray. Right-click the icon to quit.", self.icon, 4000)
            return
        self.shutdown()
        super().closeEvent(event)

    def quit_app(self):
        self.quitting = True
        self.shutdown()
        QApplication.instance().quit()

    def shutdown(self):
        if self._shut_down:
            return
        self._shut_down = True
        self.save()
        self.restart_pending = False
        if self.engine:
            self.engine.stop()
            self.engine.wait(5000)
        if self.test_executor:
            self.test_executor.close()
        if self.gpu_installer:
            self.gpu_installer.cancel.set()
            self.gpu_installer.wait(5000)


def _bundled_cuda() -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec("nvidia.cublas") is not None
    except ModuleNotFoundError:
        return False


def load_icon() -> QIcon:
    with resources.as_file(resources.files("voxcommand") / "icon.svg") as path:
        return QIcon(str(path))


def main(profile: str | None = None, hidden: bool = False, listen: bool = False):
    app = QApplication(sys.argv[:1])
    app.setApplicationName("VoxCommand")
    app.setDesktopFileName("voxcommand")
    app.setQuitOnLastWindowClosed(False)  # the tray keeps it running; quit_app exits
    if profile:
        path = Path(profile).expanduser()
    else:
        path = Path(QSettings("voxcommand", "voxcommand").value("profile", str(DEFAULT_PROFILE)))
        if not path.exists():
            path = DEFAULT_PROFILE
    if not path.exists():
        from .cli import write_example

        write_example(path)
    window = MainWindow(path)
    app.aboutToQuit.connect(window.shutdown)  # e.g. logging out
    if window.tray is None:
        app.setQuitOnLastWindowClosed(True)
    if hidden and window.tray is not None:
        window.tray.showMessage("VoxCommand", "Running in the system tray", window.icon, 3000)
    elif hidden:
        window.showMinimized()
    else:
        window.show()
    if listen:
        window.start_engine()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
