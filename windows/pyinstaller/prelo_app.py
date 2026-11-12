"""Ponto de entrada do prelo-app.exe (a janela), para o PyInstaller.

``freeze_support`` vem antes de tudo: os processos de conversão são o próprio
executável chamado de novo com ``--multiprocessing-fork``.
"""

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from prelo.app.desktop import main

    sys.exit(main())
