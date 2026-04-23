import os, logging
import sys

print(f"[log_config] cwd={os.getcwd()}", file=sys.stderr)
print(f"[log_config] logs dir will be={os.path.abspath('logs')}", file=sys.stderr)

os.makedirs("logs", exist_ok=True)
_root = logging.getLogger()
_root.setLevel(logging.INFO)

if not any(isinstance(h, logging.FileHandler) for h in _root.handlers):
    _fh = logging.FileHandler("logs/agent.log", encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    _root.addHandler(_fh)

__all__ = [
    "graph",
]
