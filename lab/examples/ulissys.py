# ulissys - Ubuntu License Scanning System

import os
import re
import json

EXTENSIONS = {".c", ".h", ".cpp"}
PATTERN = re.compile(r".*(Copyright|License).*", re.IGNORECASE)
MAX_LINES = 50


def scan_file(filepath):
    matches = []
    try:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= MAX_LINES:
                    break
                match = PATTERN.search(line)
                if match:
                    matches.append(line.strip())
    except OSError:
        pass
    return matches


def scan_directory(root="."):
    results = {}
    for dirpath, _, filenames in os.walk(root):
        for filename in filenames:
            if os.path.splitext(filename)[1] in EXTENSIONS:
                filepath = os.path.join(dirpath, filename)
                findings = scan_file(filepath)
                if findings:
                    results[filepath] = findings
    return results


if __name__ == "__main__":
    summary = scan_directory(".")
    print(json.dumps(summary, indent=2))
