import os.path
from packaging.specifiers import SpecifierSet

from . import builtin, builtin
from .packages import system_executable
from ..build_inputs import Directory, Edge, File, Phony, objectify, sourcify
from ..file_types import *
from ..iterutils import iterate, listify
from ..path import Path, Root
from ..shell import posix as pshell
from .. import version as _version

class TestCase(object):
    def __init__(self, target, options, env):
        self.target = target
        self.options = options
        self.env = env

class TestDriver(object):
    def __init__(self, target, options, env):
        self.target = target
        self.options = options
        self.env = env
        self.tests = []

class ObjectFiles(list):
    def __getitem__(self, key):
        if isinstance(key, basestring):
            key = Path(key, Root.srcdir)
        elif isinstance(key, File):
            key = key.path

        if isinstance(key, Path):
            for i in self:
                if i.creator and i.creator.file.path == key:
                    return i
            raise ValueError("{!r} not found".format(key))
        else:
            return list.__getitem__(self, key)

#####

class Compile(Edge):
    def __init__(self, build, env, name, file, include=None,
                 packages=None, options=None, lang=None, extra_deps=None):
        if name is None:
            name = os.path.splitext(file)[0]
        include = [sourcify(i, HeaderDirectory) for i in iterate(include)]

        self.file = sourcify(file, SourceFile, lang=lang)
        self.builder = env.compiler(self.file.lang)
        self.include = sum((i.includes for i in iterate(packages)), include)
        self.options = pshell.listify(options)
        self.in_shared_library = False

        target = self.builder.output_file(name, self.file.lang)
        Edge.__init__(self, build, target, extra_deps)

class Link(Edge):
    # This is just for MSBuild. XXX: Remove this?
    __project_prefixes = {
        'executable': '',
        'static_library': 'lib',
        'shared_library': 'lib',
    }

    @classmethod
    def __project(cls, name, mode):
        head, tail = os.path.split(name)
        return os.path.join(head, cls.__project_prefixes[mode] + tail)

    def __init__(self, builtins, build, env, mode, name, files, include=None,
                 libs=None, packages=None, compile_options=None,
                 link_options=None, lang=None, extra_deps=None):
        # XXX: Try to detect if a string refers to a shared lib?
        libs = [sourcify(i, Library, StaticLibrary) for i in iterate(libs)]

        self.files = builtins['object_files'](
            files, include, packages, compile_options, lang
        )
        if len(self.files) == 0:
            raise ValueError('need at least one source file')

        self.builder = env.linker((i.lang for i in self.files), mode)
        self.libs = sum((i.libraries for i in iterate(packages)), libs)
        self.options = pshell.listify(link_options)
        self.project_name = self.__project(name, mode)

        target = self.builder.output_file(name)
        if getattr(self.builder, 'post_install', None):
            target.post_install = self.builder.post_install

        build.fallback_default = target
        Edge.__init__(self, build, target, extra_deps)

class Alias(Edge):
    def __init__(self, build, name, deps=None):
        Edge.__init__(self, build, Phony(name), deps)

class Command(Edge):
    def __init__(self, build, name, cmd=None, cmds=None, extra_deps=None):
        if (cmd is None) == (cmds is None):
            raise ValueError('exactly one of "cmd" or "cmds" must be specified')
        elif cmds is None:
            cmds = [cmd]

        self.cmds = cmds
        Edge.__init__(self, build, Phony(name), extra_deps)

#####

@builtin
def source_file(name, lang=None):
    # XXX: Add a way to make a generic File object instead of a SourceFile?
    return SourceFile(name, root=Root.srcdir, lang=lang)

@builtin
def header(name):
    return HeaderFile(name, root=Root.srcdir)

@builtin
def header_directory(directory):
    return HeaderDirectory(directory, root=Root.srcdir)

@builtin.globals('build_inputs', 'env')
def object_file(build, env, name=None, file=None, *args, **kwargs):
    if file is None:
        if name is None:
            raise TypeError('expected name')
        return ObjectFile(name, root=Root.srcdir, *args, **kwargs)
    else:
        return Compile(build, env, name, file, *args, **kwargs).target

@builtin.globals('build_inputs', 'env')
def object_files(build, env, files, *args, **kwargs):
    def _compile(file, *args, **kwargs):
        return Compile(build, env, None, file, *args, **kwargs).target
    return ObjectFiles(objectify(i, ObjectFile, _compile, *args, **kwargs)
                       for i in iterate(files))

@builtin.globals('builtins', 'build_inputs', 'env')
def executable(builtins, build, env, name, files=None, *args, **kwargs):
    if files is None:
        return Executable(name, root=Root.srcdir, *args, **kwargs)
    else:
        return Link(builtins, build, env, 'executable', name, files, *args,
                    **kwargs).target

@builtin.globals('builtins', 'build_inputs', 'env')
def static_library(builtins, build, env, name, files=None, *args, **kwargs):
    if files is None:
        return StaticLibrary(name, root=Root.srcdir, *args, **kwargs)
    else:
        return Link(builtins, build, env, 'static_library', name, files, *args,
                    **kwargs).target

@builtin.globals('builtins', 'build_inputs', 'env')
def shared_library(builtins, build, env, name, files=None, *args, **kwargs):
    if files is None:
        # XXX: What to do here for Windows, which has a separate DLL file?
        return SharedLibrary(name, root=Root.srcdir, *args, **kwargs)
    else:
        rule = Link(builtins, build, env, 'shared_library', name, files, *args,
                    **kwargs)
        for i in rule.files:
            i.creator.in_shared_library = True
        return rule.target

@builtin.globals('build_inputs')
def alias(build, *args, **kwargs):
    return Alias(build, *args, **kwargs).target

@builtin.globals('build_inputs')
def command(build, *args, **kwargs):
    return Command(build, *args, **kwargs).target

#####

@builtin.globals('build_inputs')
def default(build, *args):
    if len(args) == 0:
        raise ValueError('expected at least one argument')
    build.default_targets.extend(i for i in args if i.creator)

@builtin.globals('builtins', 'build_inputs')
def install(builtins, build, *args, **kwargs):
    def _flatten(args):
        for i in args:
            for j in i.all:
                yield j

    if len(args) == 0:
        raise ValueError('expected at least one argument')
    all_files = kwargs.pop('all', True)

    for i in _flatten(args) if all_files else args:
        if isinstance(i, Directory):
            build.install_targets.directories.append(i)
        else:
            builtins['default'](i)
            build.install_targets.files.append(i)

@builtin.globals('build_inputs')
def test(build, test, options=None, environment=None, driver=None):
    test = sourcify(test, File)
    build.tests.targets.append(test)
    case = TestCase(test, pshell.listify(options), environment or {})
    (driver or build.tests).tests.append(case)
    return case

@builtin.globals('builtins', 'build_inputs', 'env')
def test_driver(builtins, build, env, driver, options=None, environment=None,
                parent=None):
    driver = objectify(driver, Executable, builtins['system_executable'])
    result = TestDriver(driver, pshell.listify(options), environment or {})
    (parent or build.tests).tests.append(result)
    return result

@builtin.globals('build_inputs')
def test_deps(build, *args):
    if len(args) == 0:
        raise ValueError('expected at least one argument')
    build.tests.extra_deps.extend(i for i in args if i.creator)

@builtin.globals('build_inputs')
def global_options(build, options, lang):
    if not lang in build.global_options:
        build.global_options[lang] = []
    build.global_options[lang].extend(pshell.listify(options))

@builtin.globals('build_inputs')
def global_link_options(build, options):
    build.global_link_options.extend(pshell.listify(options))

@builtin
def bfg9000_required_version(version=None, python_version=None):
    def ensure_specifier(v):
        return None if v is None else objectify(v, SpecifierSet, None)
    template = "{kind} version {ver} doesn't meet requirement {req}"

    version = ensure_specifier(version)
    python_version = ensure_specifier(python_version)

    if version and _version.version not in version:
        raise ValueError(template.format(
            kind='bfg9000', ver=_version.version, req=version
        ))

    if python_version and _version.python_version not in python_version:
        raise ValueError(template.format(
            kind='python', ver=_version.python_version, req=python_version
        ))
