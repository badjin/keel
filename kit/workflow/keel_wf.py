from pathlib import Path
import sys


KEEL_HOME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KEEL_HOME))

from workflow.cli import main


if __name__ == "__main__":
    raise SystemExit(main(keel_home=KEEL_HOME))
