# Copyright (c) 2014-present PlatformIO <contact@platformio.org>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# pylint: disable=too-many-arguments, too-many-statements
# pylint: disable=too-many-locals, too-many-branches

import os
from os.path import isfile, join

import click

from platformio import exception, util
from platformio.commands.debug import helpers
from platformio.managers.core import inject_contrib_pysite
from platformio.project.config import ProjectConfig
from platformio.project.helpers import (is_platformio_project,
                                        load_project_ide_data)


@click.command("debug",
               context_settings=dict(ignore_unknown_options=True),
               short_help="PIO Unified Debugger")
@click.option("-d",
              "--project-dir",
              default=os.getcwd,
              type=click.Path(exists=True,
                              file_okay=False,
                              dir_okay=True,
                              writable=True,
                              resolve_path=True))
@click.option("-c",
              "--project-conf",
              type=click.Path(exists=True,
                              file_okay=True,
                              dir_okay=False,
                              readable=True,
                              resolve_path=True))
@click.option("--environment", "-e", metavar="<environment>")
@click.option("--verbose", "-v", is_flag=True)
@click.option("--interface", type=click.Choice(["gdb"]))
@click.argument("__unprocessed", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def cli(ctx, project_dir, project_conf, environment, verbose, interface,
        __unprocessed):
    try:
        util.ensure_udev_rules()
    except NameError:
        pass
    except exception.InvalidUdevRules as e:
        for line in str(e).split("\n") + [""]:
            click.echo(
                ('~"%s\\n"' if helpers.is_mi_mode(__unprocessed) else "%s") %
                line)

    if not is_platformio_project(project_dir) and os.getenv("CWD"):
        project_dir = os.getenv("CWD")

    with util.cd(project_dir):
        config = ProjectConfig.get_instance(
            project_conf or join(project_dir, "platformio.ini"))
        config.validate(envs=[environment] if environment else None)

        env_name = environment or helpers.get_default_debug_env(config)
        env_options = config.items(env=env_name, as_dict=True)
        if not set(env_options.keys()) >= set(["platform", "board"]):
            raise exception.ProjectEnvsNotAvailable()
        debug_options = helpers.validate_debug_options(ctx, env_options)
        assert debug_options

    if not interface:
        return helpers.predebug_project(ctx, project_dir, env_name, False,
                                        verbose)

    configuration = load_project_ide_data(project_dir, env_name)
    if not configuration:
        raise exception.DebugInvalidOptions(
            "Could not load debug configuration")

    if "--version" in __unprocessed:
        result = util.exec_command([configuration['gdb_path'], "--version"])
        if result['returncode'] == 0:
            return click.echo(result['out'])
        raise exception.PlatformioException("\n".join(
            [result['out'], result['err']]))

    debug_options['load_cmds'] = helpers.configure_esp32_load_cmds(
        debug_options, configuration)

    rebuild_prog = False
    preload = debug_options['load_cmds'] == ["preload"]
    load_mode = debug_options['load_mode']
    if load_mode == "always":
        rebuild_prog = (
            preload
            or not helpers.has_debug_symbols(configuration['prog_path']))
    elif load_mode == "modified":
        rebuild_prog = (
            helpers.is_prog_obsolete(configuration['prog_path'])
            or not helpers.has_debug_symbols(configuration['prog_path']))
    else:
        rebuild_prog = not isfile(configuration['prog_path'])

    if preload or (not rebuild_prog and load_mode != "always"):
        # don't load firmware through debug server
        debug_options['load_cmds'] = []

    if rebuild_prog:
        if helpers.is_mi_mode(__unprocessed):
            click.echo('~"Preparing firmware for debugging...\\n"')
            output = helpers.GDBBytesIO()
            with helpers.capture_std_streams(output):
                helpers.predebug_project(ctx, project_dir, env_name, preload,
                                         verbose)
            output.close()
        else:
            click.echo("Preparing firmware for debugging...")
            helpers.predebug_project(ctx, project_dir, env_name, preload,
                                     verbose)

        # save SHA sum of newly created prog
        if load_mode == "modified":
            helpers.is_prog_obsolete(configuration['prog_path'])

    if not isfile(configuration['prog_path']):
        raise exception.DebugInvalidOptions("Program/firmware is missed")

    # run debugging client
    inject_contrib_pysite()
    from platformio.commands.debug.client import GDBClient, reactor

    client = GDBClient(project_dir, __unprocessed, debug_options, env_options)
    client.spawn(configuration['gdb_path'], configuration['prog_path'])

    reactor.run()

    return True
