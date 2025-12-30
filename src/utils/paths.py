import sys
import os

def get_resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

def get_config_path(filename):
    """ Config files are always external, relative to the executable/cwd """
    # In onefile mode, sys.executable is the exe path. 
    # But usually CWD is where the user ran it from.
    # We'll assume CWD for config to allow portability easily.
    return os.path.join("config", filename)
