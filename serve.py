"""Convenience launcher: python3 serve.py [port]

Downloads / caches the OWID energy dataset on first run, then starts the app.
"""
import os
import sys

if __name__ == "__main__":
    port = sys.argv[1] if len(sys.argv) > 1 else "8000"
    try:
        import location
        location.ensure_cache()
        print("OWID energy dataset ready")
    except Exception as exc:                                  # noqa: BLE001
        print(f"[warn] could not prepare OWID cache ({exc}); "
              "the Location Explorer will report the source as unavailable. "
              "Dataset upload and ML features still work.")
    os.execvp("uvicorn", ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", port])
