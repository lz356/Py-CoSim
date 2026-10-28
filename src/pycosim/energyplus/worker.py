"""Entry point of an EnergyPlus worker process.

Started by :class:`~pycosim.energyplus.component.EnergyPlusComponent` as::

    python -m pycosim.energyplus.worker <address>

with the authentication key in the ``PYCOSIM_AUTHKEY`` environment variable.
A separate interpreter is used (instead of ``multiprocessing``) so that user
scripts do not need an ``if __name__ == "__main__":`` guard and the worker
works from Jupyter notebooks as well.
"""
from __future__ import annotations

import os
import sys
import traceback
from multiprocessing.connection import Client


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if len(argv) < 2:
        print("usage: python -m pycosim.energyplus.worker <address>", file=sys.stderr)
        return 2
    address = argv[1]
    authkey = bytes.fromhex(os.environ["PYCOSIM_AUTHKEY"])
    conn = Client(address, authkey=authkey)
    try:
        cfg = conn.recv()
        root = cfg["eplus_root"]
        if root not in sys.path:
            sys.path.insert(0, root)
        from pyenergyplus.api import EnergyPlusAPI  # noqa: E402

        from .session import EPlusSession

        api = EnergyPlusAPI()
        session = EPlusSession(api, cfg, conn)
        session.run()
        return 0
    except (EOFError, BrokenPipeError, ConnectionResetError):
        return 3
    except BaseException:
        try:
            conn.send(("error", "worker", traceback.format_exc()))
        except Exception:
            pass
        return 1
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    # os._exit avoids hangs from EnergyPlus' static destructors in some versions
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
