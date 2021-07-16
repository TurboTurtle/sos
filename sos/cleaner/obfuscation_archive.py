# Copyright 2020 Red Hat, Inc. Jake Hunsaker <jhunsake@redhat.com>

# This file is part of the sos project: https://github.com/sosreport/sos
#
# This copyrighted material is made available to anyone wishing to use,
# modify, copy, or redistribute it subject to the terms and conditions of
# version 2 of the GNU General Public License.
#
# See the LICENSE file in the source distribution for further information.

import logging
import os
import shutil
import stat
import tarfile
import re

from concurrent.futures import ProcessPoolExecutor


# python older than 3.8 will hit a pickling error when we go to spawn a new
# process for extraction if this method is a part of the SoSObfuscationArchive
# class. So, the simplest solution is to remove it from the class.
def extract_archive(archive_path, tmpdir):
    archive = tarfile.open(archive_path)
    path = os.path.join(tmpdir, 'cleaner')
    archive.extractall(path)
    archive.close()
    return os.path.join(path, archive.name.split('/')[-1].split('.tar')[0])


class SoSObfuscationArchive():
    """A representation of an extracted archive or an sos archive build
    directory which is used by SoSCleaner.

    Each archive that needs to be obfuscated is loaded into an instance of this
    class. All report-level operations should be contained within this class.
    """

    file_sub_list = []
    total_sub_count = 0
    removed_file_count = 0

    def __init__(self, archive_path, tmpdir):
        self.archive_path = archive_path
        self.final_archive_path = self.archive_path
        self.tmpdir = tmpdir
        self.archive_name = self.archive_path.split('/')[-1].split('.tar')[0]
        self.ui_name = self.archive_name
        self.soslog = logging.getLogger('sos')
        self.ui_log = logging.getLogger('sos_ui')
        self.skip_list = self._load_skip_list()
        self.log_info("Loaded %s as an archive" % self.archive_path)

    def report_msg(self, msg):
        """Helper to easily format ui messages on a per-report basis"""
        self.ui_log.info("{:<50} {}".format(self.ui_name + ' :', msg))

    def _fmt_log_msg(self, msg):
        return "[cleaner:%s] %s" % (self.archive_name, msg)

    def log_debug(self, msg):
        self.soslog.debug(self._fmt_log_msg(msg))

    def log_info(self, msg):
        self.soslog.info(self._fmt_log_msg(msg))

    def _load_skip_list(self):
        """Provide a list of files and file regexes to skip obfuscation on

        Returns: list of files and file regexes
        """
        return [
            'sosreport-',
            'sys/firmware',
            'sys/fs',
            'sys/kernel/debug',
            'sys/module'
        ]

    @property
    def is_tarfile(self):
        try:
            return tarfile.is_tarfile(self.archive_path)
        except Exception:
            return False

    def remove_file(self, fname):
        """Remove a file from the archive. This is used when cleaner encounters
        a binary file, which we cannot reliably obfuscate.
        """
        full_fname = self.get_file_path(fname)
        # don't call a blank remove() here
        if full_fname:
            self.log_info("Removing binary file '%s' from archive" % fname)
            os.remove(full_fname)
            self.removed_file_count += 1

    def extract(self):
        if self.is_tarfile:
            self.report_msg("Extracting...")
            self.extracted_path = self.extract_self()
        else:
            self.extracted_path = self.archive_path
        # if we're running as non-root (e.g. collector), then we can have a
        # situation where a particular path has insufficient permissions for
        # us to rewrite the contents and/or add it to the ending tarfile.
        # Unfortunately our only choice here is to change the permissions
        # that were preserved during report collection
        if os.getuid() != 0:
            self.log_debug('Verifying permissions of archive contents')
            for dirname, dirs, files in os.walk(self.extracted_path):
                try:
                    for _dir in dirs:
                        _dirname = os.path.join(dirname, _dir)
                        _dir_perms = os.stat(_dirname).st_mode
                        os.chmod(_dirname, _dir_perms | stat.S_IRWXU)
                    for filename in files:
                        fname = os.path.join(dirname, filename)
                        # protect against symlink race conditions
                        if not os.path.exists(fname) or os.path.islink(fname):
                            continue
                        if (not os.access(fname, os.R_OK) or not
                                os.access(fname, os.W_OK)):
                            self.log_debug(
                                "Adding owner rw permissions to %s"
                                % fname.split(self.archive_path)[-1]
                            )
                            os.chmod(fname, stat.S_IRUSR | stat.S_IWUSR)
                except Exception as err:
                    self.log_debug("Error while trying to set perms: %s" % err)
        self.log_debug("Extracted path is %s" % self.extracted_path)

    def rename_top_dir(self, new_name):
        """Rename the top-level directory to new_name, which should be an
        obfuscated string that scrubs the hostname from the top-level dir
        which would be named after the unobfuscated sos report
        """
        _path = self.extracted_path.replace(self.archive_name, new_name)
        self.archive_name = new_name
        os.rename(self.extracted_path, _path)
        self.extracted_path = _path

    def get_compression(self):
        """Return the compression type used by the archive, if any. This is
        then used by SoSCleaner to generate a policy-derived compression
        command to repack the archive
        """
        if self.is_tarfile:
            if self.archive_path.endswith('xz'):
                return 'xz'
            return 'gz'
        return None

    def build_tar_file(self, method):
        """Pack the extracted archive as a tarfile to then be re-compressed
        """
        mode = 'w'
        tarpath = self.extracted_path + '-obfuscated.tar'
        compr_args = {}
        if method:
            mode += ":%s" % method
            tarpath += ".%s" % method
            if method == 'xz':
                compr_args = {'preset': 3}
            else:
                compr_args = {'compresslevel': 6}
        self.log_debug("Building tar file %s" % tarpath)
        tar = tarfile.open(tarpath, mode=mode, **compr_args)
        tar.add(self.extracted_path,
                arcname=os.path.split(self.archive_name)[1])
        tar.close()
        return tarpath

    def compress(self, method):
        """Execute the compression command, and set the appropriate final
        archive path for later reference by SoSCleaner on a per-archive basis
        """
        try:
            self.final_archive_path = self.build_tar_file(method)
        except Exception as err:
            self.log_debug("Exception while re-compressing archive: %s" % err)
            raise
        self.log_debug("Compressed to %s" % self.final_archive_path)
        try:
            self.remove_extracted_path()
        except Exception as err:
            self.log_debug("Failed to remove extraction directory: %s" % err)
            self.report_msg('Failed to remove temporary extraction directory')

    def remove_extracted_path(self):
        """After the tarball has been re-compressed, remove the extracted path
        so that we don't take up that duplicate space any longer during
        execution
        """
        def force_delete_file(action, name, exc):
            os.chmod(name, stat.S_IWUSR)
            if os.path.isfile(name):
                os.remove(name)
            else:
                shutil.rmtree(name)
        self.log_debug("Removing %s" % self.extracted_path)
        shutil.rmtree(self.extracted_path, onerror=force_delete_file)

    def extract_self(self):
        """Extract an archive into our tmpdir so that we may inspect it or
        iterate through its contents for obfuscation
        """

        with ProcessPoolExecutor(1) as _pool:
            _path_future = _pool.submit(extract_archive,
                                        self.archive_path, self.tmpdir)
            path = _path_future.result()
            return path

    def get_file_list(self):
        """Return a list of all files within the archive"""
        self.file_list = []
        for dirname, dirs, files in os.walk(self.extracted_path):
            for _dir in dirs:
                _dirpath = os.path.join(dirname, _dir)
                # catch dir-level symlinks
                if os.path.islink(_dirpath) and os.path.isdir(_dirpath):
                    self.file_list.append(_dirpath)
            for filename in files:
                self.file_list.append(os.path.join(dirname, filename))
        return self.file_list

    def get_directory_list(self):
        """Return a list of all directories within the archive"""
        dir_list = []
        for dirname, dirs, files in os.walk(self.extracted_path):
            dir_list.append(dirname)
        return dir_list

    def update_sub_count(self, fname, count):
        """Called when a file has finished being parsed and used to track
        total substitutions made and number of files that had changes made
        """
        self.file_sub_list.append(fname)
        self.total_sub_count += count

    def get_file_path(self, fname):
        """Return the filepath of a specific file within the archive so that
        it may be selectively inspected if it exists
        """
        _path = os.path.join(self.extracted_path, fname.lstrip('/'))
        return _path if os.path.exists(_path) else ''

    def should_skip_file(self, filename):
        """Checks the provided filename against a list of filepaths to not
        perform obfuscation on, as defined in self.skip_list

        Positional arguments:

            :param filename str:        Filename relative to the extracted
                                        archive root
        """

        if (not os.path.isfile(self.get_file_path(filename)) and not
                os.path.islink(self.get_file_path(filename))):
            return True

        for _skip in self.skip_list:
            if filename.startswith(_skip) or re.match(_skip, filename):
                return True
        return False

    def should_remove_file(self, fname):
        """Determine if the file should be removed or not, due to an inability
        to reliably obfuscate that file based on the filename.

        :param fname:       Filename relative to the extracted archive root
        :type fname:        ``str``

        :returns:   ``True`` if the file cannot be reliably obfuscated
        :rtype:     ``bool``
        """
        obvious_removes = [
            r'.*\.gz',  # TODO: support flat gz/xz extraction
            r'.*\.xz',
            r'.*\.bzip2',
            r'.*\.tar\..*',  # TODO: support archive unpacking
            r'.*\.txz$',
            r'.*\.tgz$',
            r'.*\.bin',
            r'.*\.journal',
            r'.*\~$'
        ]

        # if the filename matches, it is obvious we can remove them without
        # doing the read test
        for _arc_reg in obvious_removes:
            if re.match(_arc_reg, fname):
                return True

        if os.path.isfile(self.get_file_path(fname)):
            return self.file_is_binary(fname)
        # don't fail on dir-level symlinks
        return False

    def file_is_binary(self, fname):
        """Determine if the file is a binary file or not.


        :param fname:          Filename relative to the extracted archive root
        :type fname:           ``str``

        :returns:   ``True`` if file is binary, else ``False``
        :rtype:     ``bool``
        """
        with open(self.get_file_path(fname), 'tr') as tfile:
            try:
                # when opened as above (tr), reading binary content will raise
                # an exception
                tfile.read(1)
                return False
            except UnicodeDecodeError:
                return True
