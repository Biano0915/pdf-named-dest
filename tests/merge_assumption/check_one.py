"""Run the spec section 6 structural checks against one merged file.

Use this for merge tools that cannot be driven from Python (commercial GUI PDF
editors): merge A.pdf + B_orphan.pdf by hand, save the result, then point this
script at it.

    python check_one.py out/merged_manual.pdf
"""

from __future__ import annotations

import pathlib
import sys

from verify_merge import EXPECTED_PAGE_INDEX, SHARED_NAME, check

REMEDY = {
    1: (
        "The name tree entry was dropped. The merge tool does not carry named "
        "destinations across; the approach cannot work with this tool as-is."
    ),
    2: (
        "The link annotation was deleted. The merge tool prunes links whose "
        "destination it cannot resolve. Check whether that cleanup can be "
        "turned off in the tool's settings."
    ),
    3: (
        "The name survived but resolves to the wrong page. The merge tool "
        "renamed or re-mapped destinations. Check whether a naming prefix can "
        "avoid the collision."
    ),
}


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2

    path = pathlib.Path(sys.argv[1])
    if not path.exists():
        print(f"not found: {path}")
        return 2

    print(f"checking {path}")
    print(f"  looking for name  : {SHARED_NAME}")
    print(f"  expected target   : page {EXPECTED_PAGE_INDEX + 1}"
          f" (0-based index {EXPECTED_PAGE_INDEX})")
    print()

    ok, lines = check(path)
    print("\n".join(lines))
    print()
    print(f"  => {'PASS' if ok else 'FAIL'}")

    if not ok:
        print()
        print("  what to do about it:")
        for n, text in REMEDY.items():
            marker = f"[{n}]"
            if any(marker in line and "FAIL" in line for line in lines):
                print(f"    {marker} {text}")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())