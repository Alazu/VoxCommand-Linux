"""Entry point of the Windows build: VoxCommand.exe (windowed, opens the GUI) and voxcommand-cli.exe."""

import os
import sys

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")  # Hugging Face cache without Developer Mode
if sys.stdout is None:  # windowed exe: libraries that print (download progress bars) must not crash
    sys.stdout = sys.stderr = open(os.devnull, "w")
    if len(sys.argv) == 1:
        sys.argv.append("gui")

from voxcommand.cli import main  # noqa: E402

main()
