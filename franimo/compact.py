"""Gzip leftover uncompressed cache files. Implementation lives in core.compact."""
from core.compact import main  # noqa: F401
from core.http import CACHE  # noqa: F401

if __name__ == "__main__":
    raise SystemExit(main())
