# VoxCommand

An open-source alternative to VoiceAttack for Linux and Windows. Speak a phrase, and VoxCommand presses keys,
clicks, types, or runs programs for you. Speech recognition runs locally with OpenAI's **Whisper**
model (via [faster-whisper](https://github.com/SYSTRAN/faster-whisper)); nothing leaves your machine.

Input is injected through the kernel's `uinput` device, so it works on **Wayland and X11**, in
native games and under Proton/Wine. Built and tested on Fedora 45. A Windows version is also
available (see [Windows](#windows)).

## Download

Get `VoxCommand-<version>-x86_64.AppImage` from the
[Releases page](https://github.com/Alazu/VoxCommand-Linux/releases), then:

```bash
chmod +x VoxCommand-*-x86_64.AppImage
./VoxCommand-*-x86_64.AppImage
```

## Build the AppImage yourself

```bash
./build-appimage.sh          # -> dist/VoxCommand-<version>-x86_64.AppImage  (~200 MB)
```

The AppImage bundles Python, Qt, the Whisper runtime and PortAudio. Double-click it (or run it with
no arguments) for the GUI; pass arguments to use the command line, e.g.
`./VoxCommand-*.AppImage run --dry-run`. Whisper models are downloaded to `~/.cache/huggingface`
the first time they are used.

**GPU acceleration** (NVIDIA) is a separate download to keep the AppImage small: Settings tab >
GPU acceleration > **Download**, or `./VoxCommand-*.AppImage gpu install`. It fetches NVIDIA's CUDA
libraries (about 1.4 GB download, 2.1 GB on disk) from PyPI, verifies their checksums and stores
them in `~/.local/share/voxcommand/cuda`. `gpu remove` deletes them again. Only the NVIDIA driver
needs to be installed on the system. (`./build-appimage.sh --gpu` still builds an all-in-one
1.5 GB AppImage if you prefer that.)

## Windows

Download `VoxCommand-<version>-windows-x64.zip` from the
[Releases page](https://github.com/Alazu/VoxCommand-Linux/releases) (the release titled
"VoxCommand for Windows"), unzip it anywhere, and run `VoxCommand.exe`. Windows 10 or 11, 64-bit.
The build is not code-signed, so SmartScreen may warn the first time: choose **More info > Run anyway**.

- `VoxCommand.exe` opens the GUI; `voxcommand-cli.exe` is the same program for the command line
  (`voxcommand-cli.exe --help`).
- Keys and mouse clicks are sent with hardware scan codes, so DirectInput games see them.
  To control a game or app that runs as administrator, run VoxCommand as administrator too.
- Push-to-talk needs no extra permissions. Spoken replies use the Windows voices (SAPI) or Piper.
- Your profile is `%APPDATA%\VoxCommand\profile.yaml`; downloaded voices and GPU libraries go to
  `%LOCALAPPDATA%\VoxCommand`. "Start when I log in" adds a per-user Run entry in the registry.
- GPU acceleration (Settings) downloads NVIDIA's CUDA libraries for Windows; it needs an NVIDIA driver.
- Profiles are the same on both systems; key names are the Linux ones listed below.

To build it yourself on Windows with Python 3.12: `powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1`.

## Install from source (Fedora)

```bash
sudo dnf install portaudio speech-dispatcher gcc python3.12-devel kernel-headers   # one-off
cd voxcommand
uv tool install --python 3.12 '.[gui,gpu]'  # NVIDIA GPU
uv tool install --python 3.12 '.[gui]'      # or: CPU only   (drop gui for command line only)
```

(No `uv`? `sudo dnf install uv`, or use `pipx install '.[gpu]'`.) The `gpu` extra pulls in the CUDA
libraries Whisper needs; you only need the NVIDIA driver itself installed. Without a usable GPU
VoxCommand falls back to the CPU automatically, where `base.en` or `small.en` are still quick.

### Permissions

VoxCommand needs to write to `/dev/uinput`. On Fedora the logged-in desktop user normally already
has access. If `voxcommand run` reports it cannot open `/dev/uinput`, add a udev rule:

```bash
sudo cp 70-voxcommand.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger /dev/uinput
```

Push-to-talk additionally needs to *read* your keyboard/mouse: `sudo usermod -aG input $USER`, then
log out and back in.

## Use

`voxcommand gui` opens the editor: pick a profile, add commands (what you say, what it does), press
**Start listening** and watch the log to see what Whisper heard. Edits are saved automatically and
apply while listening. **Run application…** picks an installed app for a `run` action and adds an "open <app>"
phrase. **Test in 3 s** runs a command's actions without speaking, and **Dry run**
shows actions instead of performing them. Closing the window keeps VoxCommand running in the system tray; use **Quit** in the
tray menu (or Ctrl+Q) to exit.

The same things from the command line:

```bash
voxcommand init              # writes ~/.config/voxcommand/profile.yaml
voxcommand run               # listen and act   (--dry-run prints actions instead)
voxcommand listen            # just show what Whisper hears
voxcommand simulate gear up  # test matching without speaking
voxcommand check             # validate the profile, list every phrase
voxcommand devices           # list microphones
voxcommand transcribe a.wav  # which command a recording would trigger
voxcommand say "hello"       # speak with the profile's voice
```

Use `-p other.yaml` on any command to keep one profile per game. To start with your session, see
`voxcommand.service`.

## Profiles

```yaml
settings:
  model: small.en
  wake_word: null        # e.g. "computer"
  push_to_talk: null     # e.g. rightctrl, mouse4

commands:
  - name: landing gear
    phrases: ["[toggle|] landing gear", "gear [up|down]"]
    actions:
      - key: g
  - name: dictate
    phrases: ["type *"]
    actions:
      - type: "{text}"
```

**Phrases**: `[a|b]` gives alternatives, `[word|]` makes a word optional, and a trailing `*`
captures the rest of what you said as `{text}`. Matching is fuzzy (`match_threshold`), so small
mishearings still trigger the right command.

**Actions** run in order:

| Action | Effect |
| --- | --- |
| `key: ctrl+shift+f1` | Tap a key or combo. Optional `hold: 1.5` (seconds), `times: 3` |
| `press: w` / `release: w` | Hold a key down until released |
| `type: "text"` | Type text (US layout characters) |
| `mouse: left` | Click `left`/`right`/`middle`/`back`/`forward`. Optional `hold:`, `times:` |
| `scroll: -3` | Mouse wheel |
| `pause: 0.5` | Wait |
| `run: "command"` | Run a shell command (`wait: true` to block). `{text}` is shell-quoted |
| `open: "https://…?q={query}"` | Open a URL, file, folder or shortcut with its default app (no shell) |
| `say: "text"` | Speak a reply with the voice from Settings; add `voice:` to override it for one command |
| `sound: file.wav` | Play a sound (Windows: `.wav` only) |
| `listening: off` | `off`/`on`/`toggle`: while off, only commands that turn it back on work |

A command may also set `repeat: n`. Key names are letters, digits, `f1`-`f24`, `space`, `enter`,
`esc`, `tab`, `up`, `kp1`, `volumeup`... or any kernel name such as `KEY_SCROLLLOCK` / `BTN_SIDE`.

All settings and their defaults are in `voxcommand/config.py`; the generated profile documents the
common ones.

## Voices

Settings > Spoken replies picks the voice for `say` actions:

- **System voice**: speech-dispatcher / espeak-ng voices on Linux, the installed Windows voices on Windows,
  with speed, pitch and volume.
- **Natural voice (Piper)**: neural voices that run offline. **Get voices…** lists the public Piper
  catalogue (180+ voices, many languages) and downloads them to `~/.local/share/voxcommand/voices`
  (Windows: `%LOCALAPPDATA%\VoxCommand\voices`);
  **Voice file…** uses your own Piper voice (`.onnx` with its `.onnx.json` next to it).

A single `say` action can use a different voice: `- say: "Shields up"` with `voice: en_GB-alan-medium`.

## Tips

- Wrong command firing? Raise `match_threshold`, set a `wake_word`, or use `push_to_talk`.
- Game ignores key taps? Raise `key_press_ms` to 80-100.
- Want more accuracy? `model: large-v3-turbo` on a GPU. Want less latency on CPU? `base.en`.
- Other languages: use a multilingual model (`small`, `large-v3-turbo`) and set `language`.

## Development

```bash
uv venv --python 3.12 && uv pip install -e '.[gui,gpu,dev]' && .venv/bin/pytest
```

MIT licensed.
