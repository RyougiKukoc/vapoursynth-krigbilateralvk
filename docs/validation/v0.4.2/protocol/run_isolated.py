"""Run a repository test against explicitly selected libraries, without autoload."""
import os
from pathlib import Path
import runpy
import sys
import vapoursynth as vs


class IsolatedPolicy(vs.EnvironmentPolicy):
    def on_policy_registered(self, api):
        self.api = api
        self.environment = api.create_environment(vs.DISABLE_AUTO_LOADING)

    def get_current_environment(self):
        return self.environment

    def set_environment(self, environment):
        old, self.environment = self.environment, environment
        return old

    def is_alive(self, environment):
        return environment is not None


policy = IsolatedPolicy()
vs.register_policy(policy)
for library in os.environ.get('RESEARCH_EXTRA_PLUGINS', '').split(os.pathsep):
    if library:
        vs.core.std.LoadPlugin(str(Path(library).resolve()))
target = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(target.parent))
sys.argv = [str(target), *sys.argv[2:]]
try:
    runpy.run_path(str(target), run_name='__main__')
finally:
    policy.api.destroy_environment(policy.environment)
    policy.environment = None
