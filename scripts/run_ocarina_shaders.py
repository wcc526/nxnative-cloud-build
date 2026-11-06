"""Run the locked shader tools with a bounded, disposable Metal cache."""
from pathlib import Path
import runpy
import sys

compiler = Path(sys.argv[1]).resolve()
assert compiler.name == 'own_compilers.py' and compiler.parent.name == 'tools'
sys.path.insert(0, str(compiler.parents[2] / 'tools'))
import shader_cache


class BoundedMetalStore(shader_cache.Store):
    LIMIT = 128 * 1024**2

    def __init__(self, path, say=print):
        super().__init__(path, say)
        self.bytes_saved = 0

    def put(self, name, data, when=None):
        # The new full build always writes its final libraries and reflection
        # separately. Only cache duplicate MSL compilations in this job.
        if not name.startswith('metallib/'):
            return
        with self.lock:
            if self.bytes_saved + len(data) <= self.LIMIT:
                super().put(name, data, when)
                self.bytes_saved += len(data)

    def put_file(self, name, path):
        if name.startswith('metallib/') and Path(path).stat().st_size <= self.LIMIT:
            super().put_file(name, path)


shader_cache.Store = BoundedMetalStore
sys.argv = sys.argv[1:]
runpy.run_path(str(compiler), run_name='__main__')
