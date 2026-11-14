"""Field soil-loss and landslide analysis from a request JSON.

    python run.py request.json [--refresh]

See src/backend/run.py and docs/BACKEND.md.
"""
import sys

from src.backend.run import main

if __name__ == "__main__":
    sys.exit(main())
