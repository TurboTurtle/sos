# Copyright (C) 2015 Red Hat, Inc., Lee Yarwood <lyarwood@redhat.com>

# This file is part of the sos project: https://github.com/sosreport/sos
#
# This copyrighted material is made available to anyone wishing to use,
# modify, copy, or redistribute it subject to the terms and conditions of
# version 2 of the GNU General Public License.
#
# See the LICENSE file in the source distribution for further information.


from sos.plugins import Plugin, RedHatPlugin, DebianPlugin, UbuntuPlugin


class OpenStackTrove(Plugin):
    """OpenStack Trove
    """

    plugin_name = "openstack_trove"
    profiles = ('openstack', 'openstack_controller')
    option_list = []

    var_puppet_gen = "/var/lib/config-data/puppet-generated/trove"

    def setup(self):

        self.limit = self.get_option("log_size")
        if self.get_option("all_logs"):
            self.add_copy_spec([
                "/var/log/trove/",
                "/var/log/containers/trove/"
            ], sizelimit=self.limit)
        else:
            self.add_copy_spec([
                "/var/log/trove/*.log",
                "/var/log/containers/trove/*.log"
            ], sizelimit=self.limit)

        self.add_copy_spec([
            '/etc/trove/',
            self.var_puppet_gen + '/etc/trove/'
        ])

        if self.get_option("verify"):
            self.add_cmd_output("rpm -V %s" % ' '.join(self.packages))

    def postproc(self):

        protect_keys = [
            "default_password_length", "notifier_queue_password",
            "rabbit_password", "replication_password", "connection",
            "admin_password", "dns_passkey"
        ]

        regexp = r"((?m)^\s*(%s)\s*=\s*)(.*)" % "|".join(protect_keys)
        self.do_path_regex_sub("/etc/trove/*", regexp, r"\1*********")
        self.do_path_regex_sub(
            self.var_puppet_gen + "/etc/trove/*",
            regexp, r"\1*********"
        )


class DebianTrove(OpenStackTrove, DebianPlugin, UbuntuPlugin):

    packages = [
        'python-trove',
        'trove-common',
        'trove-api',
        'trove-taskmanager'
    ]

    def setup(self):
        super(DebianTrove, self).setup()


class RedHatTrove(OpenStackTrove, RedHatPlugin):

    packages = ['openstack-trove']

    def setup(self):
        super(RedHatTrove, self).setup()

# vim: set et ts=4 sw=4 :
