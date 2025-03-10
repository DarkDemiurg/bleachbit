# vim: ts=4:sw=4:expandtab
# -*- coding: UTF-8 -*-

# BleachBit
# Copyright (C) 2008-2019 Andrew Ziem
# https://www.bleachbit.org
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.


"""
Actions that perform cleaning
"""

from __future__ import absolute_import

from bleachbit import Command, FileUtilities, General, Special
from bleachbit import _, expanduser, expandvars

import glob
import logging
import os
import re
import types


if 'posix' == os.name:
    re_flags = 0
    from bleachbit import Unix
else:
    re_flags = re.IGNORECASE

logger = logging.getLogger(__name__)


def has_glob(s):
    """Checks whether the string contains any glob characters"""
    return re.search('[?*\[\]]', s) is not None


def expand_multi_var(s, variables):
    """Expand strings with potentially-multiple values.

    The placeholder is written in the format $$foo$$.

    The function always returns a list of one or more strings.
    """
    if not variables or s.find('$$') == -1:
        # The input string is missing $$ or no variables are given.
        return (s,)
    matched = False
    ret = []
    for var_key, var_values in variables.iteritems():
        sub = '$$%s$$' % var_key
        if s.find(sub) == -1:
            continue
        matched = True
        for var_value in var_values:
            modified = s.replace(sub, var_value)
            ret.append(modified)
    if matched:
        return ret
    else:
        # The string has $$, but it did not match anything
        return (s,)

#
# Plugin framework
# http://martyalchin.com/2008/jan/10/simple-plugin-framework/
#


class PluginMount(type):

    """A simple plugin framework"""

    def __init__(cls, name, bases, attrs):
        if not hasattr(cls, 'plugins'):
            cls.plugins = []
        else:
            cls.plugins.append(cls)


class ActionProvider:

    """Abstract base class for performing individual cleaning actions"""
    __metaclass__ = PluginMount

    def __init__(self, action_node, path_vars=None):
        """Create ActionProvider from CleanerML <action>"""
        pass

    def get_deep_scan(self):
        """Return a dictionary used to construct a deep scan"""
        raise StopIteration

    def get_commands(self):
        """Yield each command (which can be previewed or executed)"""
        pass


#
# base class
#
class FileActionProvider(ActionProvider):

    """Base class for providers which work on individual files"""
    action_key = '_file'
    CACHEABLE_SEARCHERS = ('walk.files',)
    cache = ('nothing', '', tuple())  # global cache <search_type, path, list_of_entries>

    def __init__(self, action_element, path_vars=None):
        """Initialize file search"""
        self.regex = action_element.getAttribute('regex')
        assert(isinstance(self.regex, (str, unicode, types.NoneType)))
        self.nregex = action_element.getAttribute('nregex')
        assert(isinstance(self.nregex, (str, unicode, types.NoneType)))
        self.wholeregex = action_element.getAttribute('wholeregex')
        assert(isinstance(self.wholeregex, (str, unicode, types.NoneType)))
        self.nwholeregex = action_element.getAttribute('nwholeregex')
        assert(isinstance(self.nwholeregex, (str, unicode, types.NoneType)))
        self.search = action_element.getAttribute('search')
        self.object_type = action_element.getAttribute('type')
        self._set_paths(action_element.getAttribute('path'), path_vars)
        self.ds = {}
        if 'deep' == self.search:
            self.ds['regex'] = self.regex
            self.ds['nregex'] = self.nregex
            self.ds['command'] = action_element.getAttribute('command')
            self.ds['path'] = self.paths[0]
            if not len(self.paths) == 1:
                logger.warning(
                    _("Deep scan does not support multi-value variable."))
        if not any([self.object_type, self.regex, self.nregex,
                    self.wholeregex, self.nwholeregex]):
            # If the filter is not needed, bypass it for speed.
            self.get_paths = self._get_paths

    def _set_paths(self, raw_path, path_vars):
        """Set the list of paths to work on"""
        self.paths = []
        # expand special $$foo$$ which may give multiple values
        for path2 in expand_multi_var(raw_path, path_vars):
            path3 = expanduser(expandvars(path2))
            if os.name == 'nt' and path3:
                # convert forward slash to backslash for compatibility with getsize()
                # and for display.  Do not convert an empty path, or it will become
                # the current directory (.).
                path3 = os.path.normpath(path3)
            self.paths.append(path3)

    def get_deep_scan(self):
        if 0 == len(self.ds):
            raise StopIteration
        yield self.ds

    def get_paths(self):
        """Process the filters: regex, nregex, type

        If a filter is defined and it fails to match, this function
        returns False. Otherwise, this function returns True."""

        # optimize tight loop, avoid slow python "."
        regex = self.regex
        nregex = self.nregex
        wholeregex = self.wholeregex
        nwholeregex = self.nwholeregex
        basename = os.path.basename
        object_type = self.object_type
        if self.regex:
            regex_c_search = re.compile(self.regex, re_flags).search
        else:
            regex_c_search = None

        if self.nregex:
            nregex_c_search = re.compile(self.nregex, re_flags).search
        else:
            nregex_c_search = None

        if self.wholeregex:
            wholeregex_c_search = re.compile(self.wholeregex, re_flags).search
        else:
            wholeregex_c_search = None

        if self.nwholeregex:
            nwholeregex_c_search = re.compile(self.nwholeregex, re_flags).search
        else:
            nwholeregex_c_search = None

        for path in self._get_paths():
            if regex and not regex_c_search(basename(path)):
                continue

            if nregex and nregex_c_search(basename(path)):
                continue

            if wholeregex and not wholeregex_c_search(path):
                continue

            if nwholeregex and nwholeregex_c_search(path):
                continue

            if object_type:
                if 'f' == object_type and not os.path.isfile(path):
                    continue
                elif 'd' == object_type and not os.path.isdir(path):
                    continue

            yield path

    def _get_paths(self):
        """Return a filtered list of files"""

        def get_file(path):
            if os.path.lexists(path):
                yield path

        def get_walk_all(top):
            """Delete files and directories inside a directory but not the top directory"""
            for expanded in glob.iglob(top):
                path = None # sentinel value
                for path in FileUtilities.children_in_directory(expanded, True):
                    yield path
                # This condition executes when there are zero iterations
                # in the loop above.
                if path is None:
                    # This is a lint checker because this scenario may
                    # indicate the cleaner developer made a mistake.
                    if os.path.isfile(expanded):
                        logger.debug(
                            _('search="walk.all" used with regular file path="%s"'),
                            expanded,
                        )

        def get_walk_files(top):
            """Delete files inside a directory but not any directories"""
            for expanded in glob.iglob(top):
                for path in FileUtilities.children_in_directory(expanded, False):
                    yield path

        def get_top(top):
            """Delete directory contents and the directory itself"""
            for f in get_walk_all(top):
                yield f
            yield top

        if 'deep' == self.search:
            raise StopIteration
        elif 'file' == self.search:
            func = get_file
        elif 'glob' == self.search:
            func = glob.iglob
        elif 'walk.all' == self.search:
            func = get_walk_all
        elif 'walk.files' == self.search:
            func = get_walk_files
        elif 'walk.top' == self.search:
            func = get_top
        else:
            raise RuntimeError("invalid search='%s'" % self.search)

        cache = self.__class__.cache
        for input_path in self.paths:
            if self.search == 'glob' and not has_glob(input_path):
                # lint for people who develop cleaners
                logger.debug(_('path="%s" is not a glob pattern'), input_path)

            # use cache
            if self.search in self.CACHEABLE_SEARCHERS and cache[0] == self.search and cache[1] == input_path:
                logger.debug(_('using cached walk for path %s'), input_path)
                for x in cache[2]:
                    yield x
                return
            else:
                if self.search in self.CACHEABLE_SEARCHERS:
                    logger.debug('not using cache because it has (%s,%s) and we want (%s,%s)',
                                 cache[0], cache[1], self.search, input_path)
                self.__class__.cache = ('cleared by', input_path, tuple())

            # build new cache
            logger.debug('%s walking %s', id(self), input_path)

            if self.search in self.CACHEABLE_SEARCHERS:
                cache = self.__class__.cache = (self.search, input_path, [])
                for path in func(input_path):
                    cache[2].append(path)
                    yield path
            else:
                for path in func(input_path):
                    yield path

    def get_commands(self):
        raise NotImplementedError('not implemented')


#
# Action providers
#


class AptAutoclean(ActionProvider):

    """Action to run 'apt-get autoclean'"""
    action_key = 'apt.autoclean'

    def __init__(self, action_element, path_vars=None):
        pass

    def get_commands(self):
        # Checking executable allows auto-hide to work for non-APT systems
        if FileUtilities.exe_exists('apt-get'):
            yield Command.Function(None,
                                   Unix.apt_autoclean,
                                   'apt-get autoclean')


class AptAutoremove(ActionProvider):

    """Action to run 'apt-get autoremove'"""
    action_key = 'apt.autoremove'

    def __init__(self, action_element, path_vars=None):
        pass

    def get_commands(self):
        # Checking executable allows auto-hide to work for non-APT systems
        if FileUtilities.exe_exists('apt-get'):
            yield Command.Function(None,
                                   Unix.apt_autoremove,
                                   'apt-get autoremove')


class AptClean(ActionProvider):

    """Action to run 'apt-get clean'"""
    action_key = 'apt.clean'

    def __init__(self, action_element, path_vars=None):
        pass

    def get_commands(self):
        # Checking executable allows auto-hide to work for non-APT systems
        if FileUtilities.exe_exists('apt-get'):
            yield Command.Function(None,
                                   Unix.apt_clean,
                                   'apt-get clean')


class ChromeAutofill(FileActionProvider):

    """Action to clean 'autofill' table in Google Chrome/Chromium"""
    action_key = 'chrome.autofill'

    def get_commands(self):
        for path in self.get_paths():
            yield Command.Function(
                path,
                Special.delete_chrome_autofill,
                _('Clean file'))


class ChromeDatabases(FileActionProvider):

    """Action to clean Databases.db in Google Chrome/Chromium"""
    action_key = 'chrome.databases_db'

    def get_commands(self):
        for path in self.get_paths():
            yield Command.Function(
                path,
                Special.delete_chrome_databases_db,
                _('Clean file'))


class ChromeFavicons(FileActionProvider):

    """Action to clean 'Favicons' file in Google Chrome/Chromium"""
    action_key = 'chrome.favicons'

    def get_commands(self):
        for path in self.get_paths():
            yield Command.Function(
                path,
                Special.delete_chrome_favicons,
                _('Clean file'))


class ChromeHistory(FileActionProvider):

    """Action to clean 'History' file in Google Chrome/Chromium"""
    action_key = 'chrome.history'

    def get_commands(self):
        for path in self.get_paths():
            yield Command.Function(
                path,
                Special.delete_chrome_history,
                _('Clean file'))


class ChromeKeywords(FileActionProvider):

    """Action to clean 'keywords' table in Google Chrome/Chromium"""
    action_key = 'chrome.keywords'

    def get_commands(self):
        for path in self.get_paths():
            yield Command.Function(
                path,
                Special.delete_chrome_keywords,
                _('Clean file'))


class Delete(FileActionProvider):

    """Action to delete files"""
    action_key = 'delete'

    def get_commands(self):
        for path in self.get_paths():
            yield Command.Delete(path)


class Ini(FileActionProvider):

    """Action to clean .ini configuration files"""
    action_key = 'ini'

    def __init__(self, action_element, path_vars=None):
        FileActionProvider.__init__(self, action_element, path_vars)
        self.section = action_element.getAttribute('section')
        self.parameter = action_element.getAttribute('parameter')
        if self.parameter == "":
            self.parameter = None

    def get_commands(self):
        for path in self.get_paths():
            yield Command.Ini(path, self.section, self.parameter)


class Journald(ActionProvider):
    """Action to run 'journalctl --vacuum-time=1'"""
    action_key = 'journald.clean'

    def __init__(self, action_element, path_vars=None):
        pass

    def get_commands(self):
        if FileUtilities.exe_exists('journalctl'):
            yield Command.Function(None, Unix.journald_clean, 'journalctl --vacuum-time=1')


class Json(FileActionProvider):

    """Action to clean JSON configuration files"""
    action_key = 'json'

    def __init__(self, action_element, path_vars=None):
        FileActionProvider.__init__(self, action_element, path_vars)
        self.address = action_element.getAttribute('address')

    def get_commands(self):
        for path in self.get_paths():
            yield Command.Json(path, self.address)


class MozillaUrlHistory(FileActionProvider):

    """Action to clean Mozilla (Firefox) URL history in places.sqlite"""
    action_key = 'mozilla.url.history'

    def get_commands(self):
        for path in self.get_paths():
            yield Command.Function(path,
                                   Special.delete_mozilla_url_history,
                                   _('Clean file'))


class OfficeRegistryModifications(FileActionProvider):

    """Action to delete LibreOffice history"""
    action_key = 'office_registrymodifications'

    def get_commands(self):
        for path in self.get_paths():
            yield Command.Function(
                path,
                Special.delete_office_registrymodifications,
                _('Clean'))


class Process(ActionProvider):

    """Action to run a process"""
    action_key = 'process'

    def __init__(self, action_element, path_vars=None):
        self.cmd = expandvars(action_element.getAttribute('cmd'))
        # by default, wait
        self.wait = True
        wait = action_element.getAttribute('wait')
        if wait and wait.lower()[0] in ('f', 'n'):
            # false or no
            self.wait = False

    def get_commands(self):

        def run_process():
            try:
                if self.wait:
                    args = self.cmd.split(' ')
                    (rc, stdout, stderr) = General.run_external(args)
                else:
                    rc = 0  # unknown because we don't wait
                    from subprocess import Popen
                    Popen(self.cmd)
            except Exception as e:
                raise RuntimeError(
                    'Exception in external command\nCommand: %s\nError: %s' % (self.cmd, str(e)))
            else:
                if not 0 == rc:
                    logger.warning('Command: %s\nReturn code: %d\nStdout: %s\nStderr: %s\n',
                                   self.cmd, rc, stdout, stderr)
            return 0
        yield Command.Function(path=None, func=run_process, label=_("Run external command: %s") % self.cmd)


class Shred(FileActionProvider):

    """Action to shred files (override preference)"""
    action_key = 'shred'

    def get_commands(self):
        for path in self.get_paths():
            yield Command.Shred(path)


class SqliteVacuum(FileActionProvider):

    """Action to vacuum SQLite databases"""
    action_key = 'sqlite.vacuum'

    def get_commands(self):
        for path in self.get_paths():
            yield Command.Function(
                path,
                FileUtilities.vacuum_sqlite3,
                # TRANSLATORS: Vacuum is a verb.  The term is jargon
                # from the SQLite database.  Microsoft Access uses
                # the term 'Compact Database' (which you may translate
                # instead).  Another synonym is 'defragment.'
                _('Vacuum'))


class Truncate(FileActionProvider):

    """Action to truncate files"""
    action_key = 'truncate'

    def get_commands(self):
        for path in self.get_paths():
            yield Command.Truncate(path)


class WinShellChangeNotify(ActionProvider):

    """Action to clean the Windows Registry"""
    action_key = 'win.shell.change.notify'

    def get_commands(self):
        from bleachbit import Windows
        yield Command.Function(
            None,
            Windows.shell_change_notify,
            None)


class Winreg(ActionProvider):

    """Action to clean the Windows Registry"""
    action_key = 'winreg'

    def __init__(self, action_element, path_vars=None):
        self.keyname = action_element.getAttribute('path')
        self.name = action_element.getAttribute('name')

    def get_commands(self):
        yield Command.Winreg(self.keyname, self.name)


class YumCleanAll(ActionProvider):

    """Action to run 'yum clean all'"""
    action_key = 'yum.clean_all'

    def __init__(self, action_element, path_vars=None):
        pass

    def get_commands(self):
        # Checking allows auto-hide to work for non-APT systems
        if not FileUtilities.exe_exists('yum'):
            raise StopIteration

        yield Command.Function(
            None,
            Unix.yum_clean,
            'yum clean all')
