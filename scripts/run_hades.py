"""Build the reviewed Hades II memory optimization host using existing encrypted delivery."""
import sys
import run_build

if __name__ == '__main__':
    run_build.INPUT_TAG = 'cloud-input-hades-ii-base'
    sys.exit(run_build.main())

