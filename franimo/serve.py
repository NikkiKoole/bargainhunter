"""Local web UI. Implementation lives in core.serve."""
from core.serve import (  # noqa: F401
    HEAVY_COLS,
    JSON_COLS,
    LISTINGS_SQL,
    WEB,
    Handler,
    listings,
    main,
    meta,
)

if __name__ == "__main__":
    raise SystemExit(main())
