import os.path
import re
import unittest

from integration import *

class TestFilesWithSpaces(IntegrationTest):
    def __init__(self, *args, **kwargs):
        IntegrationTest.__init__(self, 'files_with_spaces', *args, **kwargs)

    def test_build(self):
        self.build(executable('quad damage'))
        self.assertOutput([executable('quad damage')], 'QUAD DAMAGE!\n')

    def test_build_sub_dir(self):
        self.build(executable('another file'))
        self.assertOutput([executable('another file')], 'hello from sub dir\n')

    @skip_if_backend('msbuild')
    def test_script(self):
        self.assertRegexpMatches(
            self.build('script'),
            re.compile('^hello, world!$', re.MULTILINE)
        )

if __name__ == '__main__':
    unittest.main()
