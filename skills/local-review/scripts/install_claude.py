from pathlib import Path
import sys

from agr.runtime import install_binary


install_binary(Path(sys.argv[1]))
