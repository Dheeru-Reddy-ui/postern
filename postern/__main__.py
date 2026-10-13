"""Container entrypoint: `python -m postern` runs the component named in POSTERN_COMPONENT.

One image serves every component; Cloud Run sets POSTERN_COMPONENT per service or job.
"""

import os
import sys


def main() -> int:
    component = os.environ.get("POSTERN_COMPONENT", "")
    if component == "api":
        import uvicorn

        # Cloud Run routes traffic to $PORT on all interfaces inside the container.
        uvicorn.run(
            "postern.api.app:app",
            host="0.0.0.0",  # noqa: S104
            port=int(os.environ.get("PORT", "8080")),
            log_level="info",
        )
        return 0
    if component == "worker":
        from postern.core.worker import main as worker_main

        return worker_main()
    print(f"unknown POSTERN_COMPONENT {component!r}; expected 'api' or 'worker'", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
