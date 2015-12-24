import os

from .utils import library_macro
from .. import shell
from ..file_types import *
from ..iterutils import iterate, uniques
from ..path import Root

class MsvcCompiler(object):
    def __init__(self, env, name, command, cflags):
        self.platform = env.platform

        self.name = self.command_var = name
        self.command_name = command

        self.global_args = ['/nologo'] + cflags

    def command(self, cmd, input, output, deps=None, args=None):
        result = [cmd]
        result.extend(iterate(args))
        if deps:
            result.append('/showIncludes')
        result.extend(['/c', input])
        result.append('/Fo' + output)
        return result

    def output_file(self, name, lang):
        return ObjectFile(name + '.obj', Root.builddir, lang)

    @property
    def deps_flavor(self):
        return 'msvc'

    @property
    def library_args(self):
        return []

    def include_dir(self, directory):
        return ['/I' + directory.path]

    def system_include_dir(self, directory):
        return self.include_directory(directory)

    def link_args(self, name, mode):
        if mode == 'executable':
            return []
        elif mode in ['shared_library', 'static_library']:
            return ['/D' + library_macro(name, mode)]
        else:
            raise ValueError("unknown mode '{}'".format(mode))

class MsvcLinker(object):
    def __init__(self, env, mode, name, command, ldflags, ldlibs):
        self.platform = env.platform
        self.mode = mode

        self.name = self.command_var = 'link_' + name
        self.command_name = command
        self.link_var = 'ld'

        self.global_args = ['/nologo'] + ldflags
        self.global_libs = ldlibs

    def command(self, cmd, input, output, libs=None, args=None):
        result = [cmd]
        result.extend(iterate(args))
        result.extend(iterate(input))
        result.extend(iterate(libs))
        result.append('/OUT:' + output)
        return result

    def output_file(self, name):
        if self.mode == 'executable':
            return Executable(
                name + self.platform.executable_ext, Root.builddir
            )
        elif self.mode == 'shared_library':
            ext = self.platform.shared_library_ext
            return DllLibrary(name + ext, name + '.lib', Root.builddir)
        else:
            raise ValueError("unknown mode '{}'".format(self.mode))

    @property
    def auto_link(self):
        return True

    @property
    def mode_args(self):
        return ['/DLL'] if self.mode == 'shared_library' else []

    def lib_dirs(self, libraries):
        def get_dir(lib):
            return lib.path.parent() if isinstance(lib, Library) else lib
        dirs = uniques(get_dir(i) for i in iterate(libraries))
        return ['/LIBPATH:' + i for i in dirs]

    def link_lib(self, library):
        return [library.link.path.basename()]

    def import_lib(self, library):
        if self.mode != 'shared_library':
            return []
        return ['/IMPLIB:' + library.import_lib.path]

    def rpath(self, libraries, start):
        return []

class MsvcStaticLinker(object):
    def __init__(self, env, name, command):
        self.platform = env.platform
        self.mode = 'static_library'

        self.name = self.command_var = 'lib_' + name
        self.command_name = command
        self.link_var = 'lib'

        self.global_args = shell.split(env.getvar('LIBFLAGS', ''))

    def command(self, cmd, input, output, args=None):
        result = [cmd]
        result.extend(iterate(args))
        result.extend(iterate(input))
        result.append('/OUT:' + output)
        return result

    def output_file(self, name):
        return StaticLibrary(name + '.lib', Root.builddir)

    @property
    def mode_args(self):
        return []
