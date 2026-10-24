"""Build the reviewed Terraria diagnostic host using existing encrypted delivery."""
import sys
import run_build

if __name__ == '__main__':
    run_build.INPUT_TAG = 'cloud-input-terraria-454'
    sys.exit(run_build.main())
