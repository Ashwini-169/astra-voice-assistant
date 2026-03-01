"""Pytest configuration to enable module imports from parent directory."""
import sys
from pathlib import Path

# Add the parent directory (ai-assistant/) to sys.path so pytest can import our modules
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
