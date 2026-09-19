"""Export a static build. Implementation lives in core.export."""
from core.export import Prefixes, main, pack, write_json  # noqa: F401

if __name__ == "__main__":
    raise SystemExit(main())
