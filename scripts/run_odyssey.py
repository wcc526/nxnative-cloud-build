"""Compile Super Mario Odyssey using encrypted public Actions delivery."""
import sys
import run_build

if __name__ == "__main__":
    run_build.INPUT_TAG = "cloud-input-odyssey-130-20261025"
    sys.exit(run_build.main())
