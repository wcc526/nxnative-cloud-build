"""Compile Dave the Diver using the existing encrypted delivery workflow."""
import sys
import run_build

if __name__ == "__main__":
    run_build.INPUT_TAG = "cloud-input-dave-the-diver-1061267"
    sys.exit(run_build.main())
