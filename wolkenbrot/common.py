from .util import timeout, printr, printg, printy, random_name, SSHClient

class Builder:

    def __init__(self, client, config_params):

        self.config = config_params
        self.instance = None
        self.group_id = None
        self.key = None
        self.sec_grp = NotImplementedError
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
        self.sec_grp, self.group_id = self.make_new_group()

        printy("New key {} created".format(self.key.name))
        printy("new security group {} created".format(self.sec_grp.group_name))

        return self

    @timeout(1200, "Copying files took too long ...")
    def copy_files(self):
        if self.config.get("uploads"):
            if not self.ssh_client:  # pragma: no coverage
                self.ssh_client = SSHClient(
                    host=self.instance.public_ip_address, port=22,
                    username=self.config["user"], password="",
                    key=self.key.key_material)
            else:
                for src, dst in self.config["uploads"].items():
                    self.ssh_client.copy(src, dst)
                    printy("Successfully uploaded {} to {}".format(src, dst))

    @timeout(1200, "Configure took too long ...")
    def configure(self):
        printy("starting configuration of instance")
        if not self.ssh_client:  # pragma: no coverage
            ssh_client = SSHClient(host=self.instance.public_ip_address,
                                   port=22, username=self.config["user"],
                                   password="", key=self.key.key_material)
        else:
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
