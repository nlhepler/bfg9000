import os
import shlex

import utils

class ArLinker(object):
    def __init__(self):
        self.command_name = os.getenv('AR', 'ar')
        self.command_var = 'ar'
        self.link_var = 'ar'
        self.name = 'ar'
        self.global_args = shlex.split(os.getenv('ARFLAGS', 'cru'), posix=False)

    # TODO: Figure out a way to indicate that libs are useless here.
    def command(self, cmd, input, output, libs=None, args=None):
        result = [cmd]
        result.extend(utils.listify(args))
        result.append(output)
        result.extend(utils.listify(input))
        return result

    def output_name(self, basename):
        # TODO: Support other platform naming schemes
        return 'lib' + basename + '.a'

    @property
    def mode_args(self):
        return []