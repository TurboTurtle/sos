# Copyright 2020 Red Hat, Inc. Jake Hunsaker <jhunsake@redhat.com>

# This file is part of the sos project: https://github.com/sosreport/sos
#
# This copyrighted material is made available to anyone wishing to use,
# modify, copy, or redistribute it subject to the terms and conditions of
# version 2 of the GNU General Public License.
#
# See the LICENSE file in the source distribution for further information.

from sos.cleaner.parsers import SoSCleanerParser
from sos.cleaner.mappings.mac_map import SoSMacMap

import re


class SoSMacParser(SoSCleanerParser):
    """Handles parsing for MAC addresses"""

    name = 'MAC Parser'
    regex_patterns = [
        # IPv6
        r'(([^:|-])([0-9a-fA-F]{2}(:|-)){7}[0-9a-fA-F]{2}(\s|$))',
        r'(([^:|-])([0-9a-fA-F]{4}(:|-)){3}[0-9a-fA-F]{4}(\s|$))',
        # IPv4, avoiding matching a substring within IPv6 addresses
        r'(([^:|-])([0-9a-fA-F]{2}([:-])){5}([0-9a-fA-F]){2}(.)?(\s|$|\W))'
    ]
    obfuscated_patterns = (
        '53:4f:53',
        '534f:53'
    )
    map_file_key = 'mac_map'
    prep_map_file = 'sos_commands/networking/ip_-d_address'

    def __init__(self, conf_file=None):
        self.mapping = SoSMacMap()
        super(SoSMacParser, self).__init__(conf_file)

    def reduce_mac_match(self, match):
        """Strips away leading and trailing non-alphanum characters from any
        matched string to leave us with just the bare MAC addr
        """
        while not (match[0].isdigit() or match[0].isalpha()):
            match = match[1:]
        while not (match[-1].isdigit() or match[-1].isalpha()):
            match = match[0:-1]
        # just to be safe, call strip() to remove any padding
        return match.strip()

    def parse_line(self, line):
        count = 0
        for skip_pattern in self.skip_line_patterns:
            if re.match(skip_pattern, line, re.I):
                return line, count
        for pattern in self.regex_patterns:
            matches = [m[0] for m in re.findall(pattern, line, re.I)]
            if matches:
                count += len(matches)
                for match in matches:
                    if match.startswith(self.obfuscated_patterns):
                        # avoid double scrubbing
                        continue
                    stripped_match = self.reduce_mac_match(match)
                    new_match = self.mapping.get(stripped_match)
                    line = line.replace(stripped_match, new_match)
        return line, count
