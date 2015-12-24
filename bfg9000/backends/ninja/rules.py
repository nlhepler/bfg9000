from cStringIO import StringIO
from itertools import chain, ifilter
from packaging.specifiers import SpecifierSet

from . import version
from .syntax import *
from ... import path
from ... import safe_str
from ... import shell
from ... import iterutils
from ...builtins import find

Path = path.Path

_rule_handlers = {}
def rule_handler(rule_name):
    def decorator(fn):
        _rule_handlers[rule_name] = fn
        return fn
    return decorator

priority = 3 if version is not None else 0

def write(env, build_inputs):
    buildfile = NinjaFile()
    buildfile.variable(path_vars[path.Root.srcdir], env.srcdir, Section.path)
    for i in path.InstallRoot:
        buildfile.variable(path_vars[i], env.install_dirs[i], Section.path)

    all_rule(build_inputs.get_default_targets(), buildfile)
    for e in build_inputs.edges:
        _rule_handlers[type(e).__name__](e, build_inputs, buildfile)
    install_rule(build_inputs.install_targets, buildfile, env)
    test_rule(build_inputs.tests, buildfile, env)
    regenerate_rule(build_inputs.find_dirs, buildfile, env)

    with open(env.builddir.append('build.ninja').string(), 'w') as out:
        buildfile.write(out)

def command_build(buildfile, output, inputs=None, implicit=None,
                  order_only=None, commands=None, env=None):
    # XXX: Only make some command builds use the console pool?
    extra_kwargs = {}
    if version in SpecifierSet('>=1.5'):
        extra_kwargs['pool'] = 'console'

    if not buildfile.has_rule('command'):
        buildfile.rule(name='command', command=var('cmd'), **extra_kwargs)
    if not buildfile.has_build('PHONY'):
        buildfile.build(output='PHONY', rule='phony')

    buildfile.build(
        output=output,
        rule='command',
        inputs=inputs,
        implicit=iterutils.listify(implicit) + ['PHONY'],
        order_only=order_only,
        variables={'cmd': Commands(commands, env)}
    )

def cmd_var(cmd, buildfile):
    name = cmd.command_var
    return buildfile.variable(name, cmd.command_name, Section.command, True)

def flags_vars(name, value, buildfile):
    gflags = buildfile.variable('global_' + name, value, Section.flags, True)
    flags = buildfile.variable(name, gflags, Section.other, True)
    return gflags, flags

def all_rule(default_targets, buildfile):
    buildfile.default(['all'])
    buildfile.build(
        output='all',
        rule='phony',
        inputs=default_targets
    )

def install_rule(install_targets, buildfile, env):
    if not install_targets:
        return

    install = env.tool('install')
    mkdir_p = env.tool('mkdir_p')

    def install_line(file):
        kind = file.install_kind
        cmd = cmd_var(install, buildfile)

        if kind != 'program':
            kind = 'data'
            cmd = [cmd] + install.data_args
        cmd = buildfile.variable('install_' + kind, cmd, Section.command, True)

        src = file.path
        dst = path.install_path(file.path, file.install_root)
        return install.command(cmd, src, dst)

    def mkdir_line(dir):
        src = dir.path
        dst = path.install_path(dir.path.parent(), dir.install_root)
        return mkdir_p.copy_command(cmd_var(mkdir_p, buildfile), src, dst)

    def post_install(file):
        if file.post_install:
            cmd = cmd_var(file.post_install, buildfile)
            return file.post_install.command(cmd, file)

    commands = chain(
        (install_line(i) for i in install_targets.files),
        (mkdir_line(i) for i in install_targets.directories),
        ifilter(None, (post_install(i) for i in install_targets.files))
    )
    command_build(
        buildfile,
        output='install',
        inputs=['all'],
        commands=commands
    )

def test_rule(tests, buildfile, env):
    if not tests:
        return

    deps = []
    if tests.targets:
        buildfile.build(
            output='tests',
            rule='phony',
            inputs=tests.targets
        )
        deps.append('tests')
    deps.extend(tests.extra_deps)

    try:
        setenv = cmd_var(env.tool('setenv'), buildfile)
    except ValueError:
        setenv = None

    def build_commands(tests, collapse=False):
        def command(test, args=None):
            env_vars = shell.local_env(test.env, setenv)
            subcmd = env_vars + [test.target] + test.options + (args or [])

            if collapse:
                out = Writer(StringIO())
                out.write_shell(subcmd)
                s = out.stream.getvalue()
                if len(subcmd) > 1:
                    s = shell.quote(s)
                return safe_str.escaped_str(s)
            return subcmd

        cmd, deps = [], []
        for i in tests:
            if type(i).__name__ == 'TestDriver':
                args, moredeps = build_commands(i.tests, True)
                if i.target.creator:
                    deps.append(i.target)
                deps.extend(moredeps)
                cmd.append(command(i, args))
            else:
                cmd.append(command(i))
        return cmd, deps

    commands, moredeps = build_commands(tests.tests)
    command_build(
        buildfile,
        output='test',
        inputs=deps + moredeps,
        commands=commands,
    )

def regenerate_rule(find_dirs, buildfile, env):
    bfg9000 = cmd_var(env.tool('bfg9000'), buildfile)
    bfgpath = Path('build.bfg', path.Root.srcdir)
    depfile = None

    if find_dirs:
        find.write_depfile(env.builddir.append(find.depfile_name).string(),
                           'build.ninja', find_dirs)
        depfile = find.depfile_name

    buildfile.rule(
        name='regenerate',
        command=[bfg9000, '--regenerate', Path('.')],
        generator=True,
        depfile=depfile,
    )
    buildfile.build(
        output=Path('build.ninja'),
        rule='regenerate',
        implicit=[bfgpath]
    )

@rule_handler('Compile')
def emit_object_file(rule, build_inputs, buildfile):
    compiler = rule.builder
    global_cflags, cflags = flags_vars(
        compiler.command_var + 'flags',
        compiler.global_args +
          build_inputs.global_options.get(rule.file.lang, []),
        buildfile
    )

    variables = {}

    cflags_value = []
    cflags_value.extend(chain.from_iterable(
        compiler.include_dir(i) for i in rule.include
    ))
    cflags_value.extend(chain.from_iterable(
        compiler.system_include_dir(i) for i in rule.system_include
    ))
    cflags_value.extend(chain(rule.internal_options, rule.options))
    if cflags_value:
        variables[cflags] = [global_cflags] + cflags_value

    if not buildfile.has_rule(compiler.name):
        command_kwargs = {}
        depfile = None
        deps = None
        if compiler.deps_flavor == 'gcc':
            deps = 'gcc'
            command_kwargs['deps'] = depfile = var('out') + '.d'
        elif compiler.deps_flavor == 'msvc':
            deps = 'msvc'
            command_kwargs['deps'] = True

        buildfile.rule(name=compiler.name, command=compiler.command(
            cmd=cmd_var(compiler, buildfile), input=var('in'),
            output=var('out'), args=cflags, **command_kwargs
        ), depfile=depfile, deps=deps)

    buildfile.build(
        output=rule.target,
        rule=compiler.name,
        inputs=[rule.file],
        implicit=rule.extra_deps,
        variables=variables
    )

@rule_handler('Link')
def emit_link(rule, build_inputs, buildfile):
    linker = rule.builder
    global_ldflags, ldflags = flags_vars(
        linker.link_var + 'flags',
        linker.global_args + build_inputs.global_link_options,
        buildfile
    )

    variables = {}
    command_kwargs = {}
    ldflags_value = list(linker.mode_args)

    variables[var('output')] = rule.target.path

    if linker.mode != 'static_library':
        ldflags_value.extend(rule.options)
        ldflags_value.extend(linker.lib_dirs(rule.libs))
        ldflags_value.extend(linker.rpath(rule.libs, rule.target.path.parent()))
        ldflags_value.extend(linker.import_lib(rule.target))

        global_ldlibs, ldlibs = flags_vars(
            linker.link_var + 'libs', linker.global_libs, buildfile
        )
        command_kwargs['libs'] = ldlibs
        if rule.libs:
            libs = sum((linker.link_lib(i) for i in rule.libs), [])
            variables[ldlibs] = [global_ldlibs] + libs

    if ldflags_value:
        variables[ldflags] = [global_ldflags] + ldflags_value

    if not buildfile.has_rule(linker.name):
        buildfile.rule(name=linker.name, command=linker.command(
            cmd=cmd_var(linker, buildfile), input=var('in'),
            output=var('output'), args=ldflags, **command_kwargs
        ))

    lib_deps = [i for i in rule.libs if i.creator]
    buildfile.build(
        output=rule.target.all,
        rule=linker.name,
        inputs=rule.files,
        implicit=lib_deps + rule.extra_deps,
        variables=variables
    )

@rule_handler('Alias')
def emit_alias(rule, build_inputs, buildfile):
    buildfile.build(
        output=rule.target,
        rule='phony',
        inputs=rule.extra_deps
    )

@rule_handler('Command')
def emit_command(rule, build_inputs, buildfile):
    command_build(
        buildfile,
        output=rule.target,
        inputs=rule.extra_deps,
        commands=rule.cmds,
        env=rule.env
    )
