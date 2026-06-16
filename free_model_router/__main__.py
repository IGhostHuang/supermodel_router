"""
Allow `python -m free_model_router` to launch the gateway.
"""

from .main import main

if __name__ == "__main__":
    raise SystemExit(main())
