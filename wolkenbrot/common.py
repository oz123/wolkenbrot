# This file is part of wolkenbrot.

# Copyright (c) 2017 - 2021 Oz Tiram <oz.tiram@gmail.com>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
# ============================================================================

from .util import timeout, printr, printg, printy, random_name, SSHClient

class Builder:

    def __init__(self, client, config_params):

        self.config = config_params
        self.instance = None
        self.sec_group_id = None
        self.key = None
        self.sec_grp = None
        self.sec_group_id = None
        self.ssh_client = None

    def make_new_key(self):
        raise NotImplementedError

    def make_new_group(self):
        raise NotImplementedError

    def __enter__(self):
        """
        __enter__ is called after __init__, thus keys are only
        created if __init__ was complete.
        """
        self.key = self.make_new_key()
        sec_grp_name, self.sec_group_id, self.sec_grp = self.make_new_group()
        assert self.sec_grp
        assert self.sec_group_id
        printy("New key {} created".format(self.key.name))
        printy("new security group {} created".format(sec_grp_name))

        return self

    @timeout(1200, "Copying files took too long ...")
    def copy_files(self):
        if self.config.get("uploads"):
            for src, dst in self.config["uploads"].items():
                self.ssh_client.copy(src, dst)
                printy("Successfully uploaded {} to {}".format(src, dst))

    @timeout(1200, "Configure took too long ...")
    def configure(self):
        ssh_client = self.ssh_client

        for command in self.config["commands"]:
            printy("Executing: {}".format(command))
            command_result = ssh_client.execute(command)
            ok = command_result['retval'] == 0
            if ok:
                printg("Command '{}' succeeded".format(command))
            else:
                printr("Command '{}' failed".format(command))
            # if 'out' in command_result:
            #    print(''.join(command_result['out']))

            # if not ok:
            #     if 'err' in command_result:
            #        print("Here are the errors:")
            #        print(''.join(command_result['err']))

        printy("Finished configuration of instance")
