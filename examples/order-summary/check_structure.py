import importlib.util
import json
from pathlib import Path
import sys

skill = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("business", skill / "scripts/summarize.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print(json.dumps({"imported": callable(module.summarize), "skill_exists": (skill / "SKILL.md").is_file()}))
