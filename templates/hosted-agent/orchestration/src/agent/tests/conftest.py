import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # the template's src/agent
sys.path.insert(0, str(Path(__file__).resolve().parent))      # fakes.py
