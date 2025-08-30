import json
import os
import platform
import sys


def runtime_snapshot() -> dict:
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "cwd": os.getcwd(),
        "executable": sys.executable,
        "sys_path_head": sys.path[:5],
        "env_hints": {
            k: v
            for k, v in os.environ.items()
            if k.upper().startswith("HH") or k.upper().startswith("PYTHON")
        },
    }


if __name__ == "__main__":
    print(json.dumps(runtime_snapshot(), indent=2, ensure_ascii=False))
