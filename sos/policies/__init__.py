import os
import platform
import time
import json
import tempfile
import random
import string

from pwd import getpwuid
from sos.policies.package_managers import PackageManager
from sos.utilities import ImporterHelper, import_module, get_human_readable
from sos.report.plugins import IndependentPlugin, ExperimentalPlugin
from sos.options import SoSOptions
from sos import _sos as _
from textwrap import fill

PRESETS_PATH = "/etc/sos/presets.d"


def import_policy(name):
    policy_fqname = "sos.policies.distros.%s" % name
    try:
        return import_module(policy_fqname, Policy)
    except ImportError:
        return None


def load(cache={}, sysroot=None, init=None, probe_runtime=True,
         remote_exec=None, remote_check=''):
    if 'policy' in cache:
        return cache.get('policy')

    import sos.policies.distros
    helper = ImporterHelper(sos.policies.distros)
    for module in helper.get_modules():
        for policy in import_policy(module):
            if policy.check(remote=remote_check):
                cache['policy'] = policy(sysroot=sysroot, init=init,
                                         probe_runtime=probe_runtime,
                                         remote_exec=remote_exec)

    if 'policy' not in cache:
        cache['policy'] = GenericPolicy()

    return cache['policy']


#: Constants for on-disk preset fields
DESC = "desc"
NOTE = "note"
OPTS = "args"


class PresetDefaults(object):
    """Preset command line defaults to allow for quick reference to sets of
    commonly used options

    :param name: The name of the new preset
    :type name: ``str``

    :param desc: A description for the new preset
    :type desc: ``str``

    :param note: Note for the new preset
    :type note: ``str``

    :param opts: Options set for the new preset
    :type opts: ``SoSOptions``
    """
    #: Preset name, used for selection
    name = None
    #: Human readable preset description
    desc = None
    #: Notes on preset behaviour
    note = None
    #: Options set for this preset
    opts = SoSOptions()

    #: ``True`` if this preset if built-in or ``False`` otherwise.
    builtin = True

    def __str__(self):
        """Return a human readable string representation of this
            ``PresetDefaults`` object.
        """
        return ("name=%s desc=%s note=%s opts=(%s)" %
                (self.name, self.desc, self.note, str(self.opts)))

    def __repr__(self):
        """Return a machine readable string representation of this
            ``PresetDefaults`` object.
        """
        return ("PresetDefaults(name='%s' desc='%s' note='%s' opts=(%s)" %
                (self.name, self.desc, self.note, repr(self.opts)))

    def __init__(self, name="", desc="", note=None, opts=SoSOptions()):
        """Initialise a new ``PresetDefaults`` object with the specified
            arguments.

            :returns: The newly initialised ``PresetDefaults``
        """
        self.name = name
        self.desc = desc
        self.note = note
        self.opts = opts

    def write(self, presets_path):
        """Write this preset to disk in JSON notation.

        :param presets_path: the directory where the preset will be written
        :type presets_path: ``str``
        """
        if self.builtin:
            raise TypeError("Cannot write built-in preset")

        # Make dictionaries of PresetDefaults values
        odict = self.opts.dict()
        pdict = {self.name: {DESC: self.desc, NOTE: self.note, OPTS: odict}}

        if not os.path.exists(presets_path):
            os.makedirs(presets_path, mode=0o755)

        with open(os.path.join(presets_path, self.name), "w") as pfile:
            json.dump(pdict, pfile)

    def delete(self, presets_path):
        """Delete a preset from disk

        :param presets_path: the directory where the preset is saved
        :type presets_path: ``str``
        """
        os.unlink(os.path.join(presets_path, self.name))


NO_PRESET = 'none'
NO_PRESET_DESC = 'Do not load a preset'
NO_PRESET_NOTE = 'Use to disable automatically loaded presets'

GENERIC_PRESETS = {
    NO_PRESET: PresetDefaults(name=NO_PRESET, desc=NO_PRESET_DESC,
                              note=NO_PRESET_NOTE, opts=SoSOptions())
}


class Policy(object):
    """Policies represent distributions that sos supports, and define the way
    in which sos behaves on those distributions. A policy should define at
    minimum a way to identify the distribution, and a package manager to allow
    for package based plugin enablement.

    Policies also control preferred ContainerRuntime()'s, upload support to
    default locations for distribution vendors, disclaimer text, and default
    presets supported by that distribution or vendor's products.

    Every Policy will also need at least one "tagging class" for plugins.

    :param sysroot: Set the sysroot for the system, if not /
    :type sysroot: ``str`` or ``None``

    :param probe_runtime: Should the Policy try to load a ContainerRuntime
    :type probe_runtime: ``bool``

    :cvar distro: The name of the distribution the Policy represents
    :vartype distro: ``str``

    :cvar vendor: The name of the vendor producing the distribution
    :vartype vendor: ``str``

    :cvar vendor_url: URL for the vendor's website, or support portal
    :vartype vendor_url: ``str``

    :cvar vendor_text: Additional text to add to the banner message
    :vartype vendor_text: ``str``

    :cvar name_pattern: The naming pattern to be used for naming archives
                        generated by sos. Values of `legacy`, and `friendly`
                        are preset patterns. May also be set to an explicit
                        custom pattern, see `get_archive_name()`
    :vartype name_pattern: ``str``
    """

    msg = _("""\
This command will collect system configuration and diagnostic information \
from this %(distro)s system.

For more information on %(vendor)s visit:

  %(vendor_url)s

The generated archive may contain data considered sensitive and its content \
should be reviewed by the originating organization before being passed to \
any third party.

%(changes_text)s

%(vendor_text)s
""")

    distro = "Unknown"
    vendor = "Unknown"
    vendor_url = "http://www.example.com/"
    vendor_text = ""
    PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    default_scl_prefix = ""
    name_pattern = 'legacy'
    presets = {"": PresetDefaults()}
    presets_path = PRESETS_PATH
    _in_container = False
    _host_sysroot = '/'

    def __init__(self, sysroot=None, probe_runtime=True):
        """Subclasses that choose to override this initializer should call
        super() to ensure that they get the required platform bits attached.
        super(SubClass, self).__init__(). Policies that require runtime
        tests to construct PATH must call self.set_exec_path() after
        modifying PATH in their own initializer."""
        self._parse_uname()
        self.case_id = None
        self.probe_runtime = probe_runtime
        self.package_manager = PackageManager()
        self.valid_subclasses = [IndependentPlugin]
        self.set_exec_path()
        self._host_sysroot = sysroot
        self.register_presets(GENERIC_PRESETS)

    def check(self, remote=''):
        """
        This function is responsible for determining if the underlying system
        is supported by this policy.

        If `remote` is provided, it should be the contents of os-release from
        a remote host, or a similar vendor-specific file that can be used in
        place of a locally available file.

        :returns: ``True`` if the Policy should be loaded, else ``False``
        :rtype: ``bool``
        """
        return False

    def in_container(self):
        """Are we running inside a container?

        :returns: ``True`` if in a container, else ``False``
        :rtype: ``bool``
        """
        return self._in_container

    def host_sysroot(self):
        """Get the host's default sysroot

        :returns: Host sysroot
        :rtype: ``str`` or ``None``
        """
        return self._host_sysroot

    def dist_version(self):
        """
        Return the OS version
        """
        pass

    def get_preferred_archive(self):
        """
        Return the class object of the prefered archive format for this
        platform
        """
        from sos.archive import TarFileArchive
        return TarFileArchive

    def get_archive_name(self):
        """
        This function should return the filename of the archive without the
        extension.

        This uses the policy's `name_pattern` attribute to determine the name.
        There are two pre-defined naming patterns - `legacy` and `friendly`
        that give names like the following:

        * legacy - `sosreport-tux.123456-20171224185433`
        * friendly - `sosreport-tux-mylabel-123456-2017-12-24-ezcfcop.tar.xz`

        A custom name_pattern can be used by a policy provided that it
        defines name_pattern using a format() style string substitution.

        Usable substitutions are:

            * name  - the short hostname of the system
            * label - the label given by --label
            * case  - the case id given by --case-id or --ticker-number
            * rand  - a random string of 7 alpha characters

        Note that if a datestamp is needed, the substring should be set
        in `name_pattern` in the format accepted by ``strftime()``.

        :returns: A name to be used for the archive, as expanded from
                  the Policy `name_pattern`
        :rtype: ``str``
        """
        name = self.get_local_name().split('.')[0]
        case = self.case_id
        label = self.commons['cmdlineopts'].label
        date = ''
        rand = ''.join(random.choice(string.ascii_lowercase) for x in range(7))

        if self.name_pattern == 'legacy':
            nstr = "sosreport-{name}{case}{date}"
            case = '.' + case if case else ''
            date = '-%Y%m%d%H%M%S'
        elif self.name_pattern == 'friendly':
            nstr = "sosreport-{name}{label}{case}{date}-{rand}"
            case = '-' + case if case else ''
            label = '-' + label if label else ''
            date = '-%Y-%m-%d'
        else:
            nstr = self.name_pattern

        nstr = nstr.format(
            name=name,
            label=label,
            case=case,
            date=date,
            rand=rand
        )
        return self.sanitize_filename(time.strftime(nstr))

    # for some specific binaries like "xz", we need to determine package
    # providing it; that is policy specific. By default return the binary
    # name itself until particular policy overwrites it
    def _get_pkg_name_for_binary(self, binary):
        return binary

    def get_cmd_for_compress_method(self, method, threads):
        """Determine the command to use for compressing the archive

        :param method: The compression method/binary to use
        :type method: ``str``

        :param threads: Number of threads compression should use
        :type threads: ``int``

        :returns: Full command to use to compress the archive
        :rtype: ``str``
        """
        cmd = method
        if cmd.startswith("xz"):
            # XZ set compression to -2 and use threads
            cmd = "%s -2 -T%d" % (cmd, threads)
        return cmd

    def get_tmp_dir(self, opt_tmp_dir):
        if not opt_tmp_dir:
            return tempfile.gettempdir()
        return opt_tmp_dir

    def get_default_scl_prefix(self):
        return self.default_scl_prefix

    def match_plugin(self, plugin_classes):
        """Determine what subclass of a Plugin should be used based on the
        tagging classes assigned to the Plugin

        :param plugin_classes: The classes that the Plugin subclasses
        :type plugin_classes: ``list``

        :returns: The first subclass that matches one of the Policy's
                  `valid_subclasses`
        :rtype: A tagging class for Plugins
        """
        if len(plugin_classes) > 1:
            for p in plugin_classes:
                # Give preference to the first listed tagging class
                # so that e.g. UbuntuPlugin is chosen over DebianPlugin
                # on an Ubuntu installation.
                if issubclass(p, self.valid_subclasses[0]):
                    return p
        return plugin_classes[0]

    def validate_plugin(self, plugin_class, experimental=False):
        """
        Verifies that the plugin_class should execute under this policy

        :param plugin_class: The tagging class being checked
        :type plugin_class: A Plugin() tagging class

        :returns: ``True`` if the `plugin_class` is allowed by the policy
        :rtype: ``bool``
        """
        valid_subclasses = [IndependentPlugin] + self.valid_subclasses
        if experimental:
            valid_subclasses += [ExperimentalPlugin]
        return any(issubclass(plugin_class, class_) for
                   class_ in valid_subclasses)

    def pre_work(self):
        """
        This function is called prior to collection.
        """
        pass

    def post_work(self):
        """
        This function is called after the sosreport has been generated.
        """
        pass

    def pkg_by_name(self, pkg):
        """Wrapper to retrieve a package from the Policy's package manager

        :param pkg: The name of the package
        :type pkg: ``str``

        :returns: The first package that matches `pkg`
        :rtype: ``str``
        """
        return self.package_manager.pkg_by_name(pkg)

    def _parse_uname(self):
        (system, node, release,
         version, machine, processor) = platform.uname()
        self.system = system
        self.hostname = node
        self.release = release
        self.smp = version.split()[1] == "SMP"
        self.machine = machine

    def set_commons(self, commons):
        """Set common host data for the Policy to reference
        """
        self.commons = commons

    def _set_PATH(self, path):
        os.environ['PATH'] = path

    def set_exec_path(self):
        self._set_PATH(self.PATH)

    def is_root(self):
        """This method should return true if the user calling the script is
        considered to be a superuser

        :returns: ``True`` if user is superuser, else ``False``
        :rtype: ``bool``
        """
        return (os.getuid() == 0)

    def get_preferred_hash_name(self):
        """Returns the string name of the hashlib-supported checksum algorithm
        to use"""
        return "md5"

    def display_results(self, archive, directory, checksum, archivestat=None,
                        map_file=None):
        """Display final information about a generated archive

        :param archive: The name of the archive that was generated
        :type archive: ``str``

        :param directory: The build directory for sos if --build was used
        :type directory: ``str``

        :param checksum: The checksum of the archive
        :type checksum: ``str``

        :param archivestat: stat() information for the archive
        :type archivestat: `os.stat_result`

        :param map_file: If sos clean was invoked, the location of the mapping
                         file for this run
        :type map_file: ``str``
        """
        # Logging is already shutdown and all terminal output must use the
        # print() call.

        # make sure a report exists
        if not archive and not directory:
            return False

        self._print()

        if map_file:
            self._print(_("A mapping of obfuscated elements is available at"
                          "\n\t%s\n" % map_file))

        if archive:
            self._print(_("Your sosreport has been generated and saved "
                          "in:\n\t%s\n") % archive, always=True)
            self._print(_(" Size\t%s") %
                        get_human_readable(archivestat.st_size))
            self._print(_(" Owner\t%s") %
                        getpwuid(archivestat.st_uid).pw_name)
        else:
            self._print(_("Your sosreport build tree has been generated "
                          "in:\n\t%s\n") % directory, always=True)
        if checksum:
            self._print(" " + self.get_preferred_hash_name() + "\t" + checksum)
            self._print()
            self._print(_("Please send this file to your support "
                          "representative."))
        self._print()

    def _print(self, msg=None, always=False):
        """A wrapper around print that only prints if we are not running in
        quiet mode"""
        if always or not self.commons['cmdlineopts'].quiet:
            if msg:
                print(msg)
            else:
                print()

    def get_msg(self):
        """This method is used to prepare the preamble text to display to
        the user in non-batch mode. If your policy sets self.distro that
        text will be substituted accordingly. You can also override this
        method to do something more complicated.

        :returns: Formatted banner message string
        :rtype: ``str``
        """
        if self.commons['cmdlineopts'].allow_system_changes:
            changes_text = "Changes CAN be made to system configuration."
        else:
            changes_text = "No changes will be made to system configuration."
        width = 72
        _msg = self.msg % {'distro': self.distro, 'vendor': self.vendor,
                           'vendor_url': self.vendor_url,
                           'vendor_text': self.vendor_text,
                           'tmpdir': self.commons['tmpdir'],
                           'changes_text': changes_text}
        _fmt = ""
        for line in _msg.splitlines():
            _fmt = _fmt + fill(line, width, replace_whitespace=False) + '\n'
        return _fmt

    def register_presets(self, presets, replace=False):
        """Add new presets to this policy object.

            Merges the presets dictionary ``presets`` into this ``Policy``
            object, or replaces the current presets if ``replace`` is
            ``True``.

            ``presets`` should be a dictionary mapping ``str`` preset names
            to ``<class PresetDefaults>`` objects specifying the command
            line defaults.

            :param presets: dictionary of presets to add or replace
            :param replace: replace presets rather than merge new presets.
        """
        if replace:
            self.presets = {}
        self.presets.update(presets)

    def find_preset(self, preset):
        """Find a preset profile matching the specified preset string.

            :param preset: a string containing a preset profile name.
            :returns: a matching PresetProfile.
        """
        # FIXME: allow fuzzy matching?
        for match in self.presets.keys():
            if match == preset:
                return self.presets[match]

        return None

    def probe_preset(self):
        """Return a ``PresetDefaults`` object matching the runing host.

            Stub method to be implemented by derived policy classes.

            :returns: a ``PresetDefaults`` object.
        """
        return self.presets[NO_PRESET]

    def load_presets(self, presets_path=None):
        """Load presets from disk.

            Read JSON formatted preset data from the specified path,
            or the default location at ``/var/lib/sos/presets``.

            :param presets_path: a directory containing JSON presets.
        """
        presets_path = presets_path or self.presets_path
        if not os.path.exists(presets_path):
            return
        for preset_path in os.listdir(presets_path):
            preset_path = os.path.join(presets_path, preset_path)

            with open(preset_path) as pf:
                try:
                    preset_data = json.load(pf)
                except ValueError:
                    continue

            for preset in preset_data.keys():
                pd = PresetDefaults(preset, opts=SoSOptions())
                data = preset_data[preset]
                pd.desc = data[DESC] if DESC in data else ""
                pd.note = data[NOTE] if NOTE in data else ""

                if OPTS in data:
                    for arg in data[OPTS]:
                        setattr(pd.opts, arg, data[OPTS][arg])
                pd.builtin = False
                self.presets[preset] = pd

    def add_preset(self, name=None, desc=None, note=None, opts=SoSOptions()):
        """Add a new on-disk preset and write it to the configured
            presets path.

            :param preset: the new PresetDefaults to add
        """
        presets_path = self.presets_path

        if not name:
            raise ValueError("Preset name cannot be empty")

        if name in self.presets.keys():
            raise ValueError("A preset with name '%s' already exists" % name)

        preset = PresetDefaults(name=name, desc=desc, note=note, opts=opts)
        preset.builtin = False
        self.presets[preset.name] = preset
        preset.write(presets_path)

    def del_preset(self, name=""):
        if not name or name not in self.presets.keys():
            raise ValueError("Unknown profile: '%s'" % name)

        preset = self.presets[name]

        if preset.builtin:
            raise ValueError("Cannot delete built-in preset '%s'" %
                             preset.name)

        preset.delete(self.presets_path)
        self.presets.pop(name)


class GenericPolicy(Policy):
    """This Policy will be returned if no other policy can be loaded. This
    should allow for IndependentPlugins to be executed on any system"""

    def get_msg(self):
        return self.msg % {'distro': self.system}


# vim: set et ts=4 sw=4 :
