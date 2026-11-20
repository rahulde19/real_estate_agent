"""Entry point for hosting (Streamlit Community Cloud: main file `streamlit_app.py`). Runs src/homefinder/ui/app.py.

It puts `src/` on the import path first, so the app works without the package being pip-installed.
Locally you can still run `streamlit run src/homefinder/ui/app.py`.
"""
import runpy
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT / "src"))
runpy.run_path(str(_ROOT / "src" / "homefinder" / "ui" / "app.py"), run_name="__main__")
