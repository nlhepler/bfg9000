import os
import re
import sys
from collections import OrderedDict, namedtuple
from itertools import chain

import toolchains.cc
from platform import target_name

cc = toolchains.cc.CcCompiler() # TODO: make this replaceable

__rule_handlers__ = {}
def rule_handler(rule_name):
    def decorator(fn):
        __rule_handlers__[rule_name] = fn
        return fn
    return decorator

NinjaRule = namedtuple('NinjaRule', ['command', 'depfile'])
NinjaBuild = namedtuple('NinjaBuild', ['rule', 'inputs', 'implicit',
                                       'variables'])
class NinjaVariable(object):
    def __init__(self, name):
        self.name = re.sub('/', '_', name)

    def use(self):
        return '${}'.format(self.name)

    def __str__(self):
        return self.use()

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, rhs):
        return self.name == rhs.name

    def __ne__(self, rhs):
        return self.name != rhs.name

class NinjaWriter(object):
    def __init__(self):
        self._variables = OrderedDict()
        self._rules = OrderedDict()
        self._builds = OrderedDict()

    def variable(self, name, value):
        if not isinstance(name, NinjaVariable):
            name = NinjaVariable(name)
        if self.has_variable(name):
            raise RuntimeError('variable "{}" already exists'.format(name))
        self._variables[name] = value

    def has_variable(self, name):
        if not isinstance(name, NinjaVariable):
            name = NinjaVariable(name)
        return name in self._variables

    def rule(self, name, command, depfile=None):
        if self.has_rule(name):
            raise RuntimeError('rule "{}" already exists'.format(name))
        self._rules[name] = NinjaRule(command, depfile)

    def has_rule(self, name):
        return name in self._rules

    def build(self, output, rule, inputs=None, implicit=None, variables=None):
        real_variables = {}
        if variables:
            for k, v in variables.iteritems():
                if not isinstance(k, NinjaVariable):
                    k = NinjaVariable(k)
                real_variables[k] = v

        if self.has_build(output):
            raise RuntimeError('build for "{}" already exists'.format(output))
        self._builds[output] = NinjaBuild(rule, inputs, implicit,
                                          real_variables)

    def has_build(self, name):
        return name in self._builds

    def _write_variable(self, out, name, value, indent=0):
        out.write('{indent}{name} = {value}\n'.format(
            indent='  ' * indent, name=name.name, value=value
        ))

    def _write_rule(self, out, name, command, depfile):
        out.write('rule {}\n'.format(name))
        self._write_variable(out, NinjaVariable('command'), command, 1)
        if depfile:
            self._write_variable(out, NinjaVariable('depfile'), depfile, 1)

    def _write_build(self, out, name, rule, inputs, implicit, variables):
        out.write('build {output}: {rule}'.format(output=name, rule=rule))

        for i in inputs or []:
            out.write(' ' + i)

        first = True
        for i in implicit or []:
            if first:
                first = False
                out.write(' |')
            out.write(' ' + i)

        out.write('\n')

        if variables:
            for k, v in variables.iteritems():
                self._write_variable(out, k, v, 1)

    def write(self, out):
        for name, value in self._variables.iteritems():
            self._write_variable(out, name, value)
        if self._variables:
            out.write('\n')

        for name, rule in self._rules.iteritems():
            self._write_rule(out, name, *rule)
            out.write('\n')

        for name, build in self._builds.iteritems():
            self._write_build(out, name, *build)

def write(path, edges):
    writer = NinjaWriter()
    for e in edges:
        __rule_handlers__[type(e).__name__](writer, e)
    with open(os.path.join(path, 'build.ninja'), 'w') as out:
        writer.write(out)

def cmd_var(writer, lang):
    cmd, varname = cc.command_name(lang)
    var = NinjaVariable(varname)
    if not writer.has_variable(var):
        writer.variable(var, cmd)
    return var

@rule_handler('Compile')
def emit_object_file(writer, rule):
    cmd = cmd_var(writer, rule.file.lang)
    rulename = cmd.name
    cflags = NinjaVariable('{}flags'.format(cmd.name))

    if not writer.has_rule(rulename):
        writer.rule(name=rulename, command=cc.compile_command(
            cmd=cmd, input='$in', output='$out', dep='$out.d',
            prevars=cflags
        ), depfile='$out.d')

    variables = {}
    cflags_value = []
    if rule.target.in_library:
        cflags_value.append(cc.library_flag())
    if rule.options:
        cflags_value.append(rule.options)
    if cflags_value:
        variables[cflags] = ' '.join(cflags_value)

    writer.build(output=target_name(rule.target), rule=rulename,
                 inputs=[target_name(rule.file)],
                 variables=variables)

@rule_handler('Link')
def emit_link(writer, rule):
    cmd = cmd_var(writer, (i.lang for i in rule.files))

    if type(rule.target).__name__ == 'Library':
        rulename = '{}_linklib'.format(cmd.name)
        mode = 'library'
    else:
        rulename = '{}_link'.format(cmd.name)
        mode = 'executable'

    cflags = NinjaVariable('{}flags'.format(cmd.name))
    libs_var = NinjaVariable('libs')
    ldflags = NinjaVariable('ldflags')

    if not writer.has_rule(rulename):
        writer.rule(name=rulename, command=cc.link_command(
            cmd=cmd, mode=mode, input='$in', output='$out',
            prevars=cflags, postvars=[libs_var, ldflags]
        ))

    variables = {}
    if rule.libs:
        variables[libs_var] = cc.link_libs(rule.libs)
    if rule.compile_options:
        variables[cflags] = rule.compile_options
    if rule.link_options:
        variables[ldflags] = rule.link_options

    writer.build(
        output=target_name(rule.target), rule=rulename,
        inputs=(target_name(i) for i in rule.files),
        implicit=(target_name(i) for i in rule.libs if not i.external),
        variables=variables
    )

@rule_handler('Alias')
def emit_alias(writer, rule):
    writer.build(
        output=target_name(rule.target), rule='phony',
        inputs=[target_name(i) for i in rule.deps]
    )

@rule_handler('Command')
def emit_command(writer, rule):
    if not writer.has_rule('command'):
        writer.rule(name='command', command='$cmd')
        writer.build(
            output=rule.target.name, rule='command',
            inputs=(target_name(i) for i in rule.deps),
            variables={'cmd': ' && '.join(rule.cmd)}
        )
