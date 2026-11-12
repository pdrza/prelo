"""Ponto de entrada do prelo.exe (linha de comando), para o PyInstaller.

``freeze_support`` vem antes de tudo: a renderização em paralelo chama o próprio
executável de novo com ``--multiprocessing-fork``.
"""

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from prelo.cli import main

    sys.exit(main())
