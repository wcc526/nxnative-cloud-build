"""Compile Dead Cells using encrypted public Actions delivery."""
import sys
import run_build

if __name__ == "__main__":
    run_build.INPUT_TAG = "cloud-input-dead-cells-1172-20261024"
    sys.exit(run_build.main())
