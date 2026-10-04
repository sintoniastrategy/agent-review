from pathlib import Path
import sys

from agr.codex import install_package


install_package(Path(sys.argv[1]))
