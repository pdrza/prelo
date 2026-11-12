"""``python -m prelo`` abre o app; ``python -m prelo convert ...`` usa a linha de comando."""

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    if len(sys.argv) > 1:
        from prelo.cli import main
    else:
        from prelo.app.desktop import main
    sys.exit(main())
