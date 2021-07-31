import time
from datetime import date, datetime
from .util import timeout, printr, printg, printy, random_name, SSHClient

from paramiko.ssh_exception import (NoValidConnectionsError,
                                    AuthenticationException)
import paramiko


class OpenStackBuilder:

    def __init__(self, client, config_params):
        self.client = client
        self.name = config_params["name"]
        self.desc = config_params["description"]
        self.region = config_params["region"]
        self.instance_type = config_params["instance_type"]
        self.image = config_params["base_image"]
        self.config = config_params
        self.tags = config_params.get("tags")
        self.key = None
        self.instance = None
        self.ssh_client = None
        self.sec_group_id = None

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

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def make_new_key(self):
        print("Creating keypair for imaging machine...")
        key_name = random_name("tmp_key_", 10)
        if self.client.get_keypair(key_name):
            self.client.delete_keypair(key_name)
        keypair = self.client.create_keypair(key_name)
        return keypair

    def make_new_group(self):
        print("Creating security group")
        name = 'wolkenbrot-image-creator-{}'.format(str(date.today()))
        if self.client.get_security_group(name):
            self.client.delete_security_group(name)

        sec_group = self.client.create_security_group(name, 'temporary security '
                                                     'for builder.')

        print("Creating security group rules")
        # allow outgoing TCP
        self.client.create_security_group_rule(sec_group.id, 1, 65535, 'tcp',
                                              '0.0.0.0/0', direction='egress')
        # allow outgoing UDP
        self.client.create_security_group_rule(sec_group.id, 1, 65535, 'udp',
                                              '0.0.0.0/0', direction='egress')
        # allow ingoing SSH
        self.client.create_security_group_rule(sec_group.id, 22, 22, 'tcp',
                                              '0.0.0.0/0', direction='ingress')

        return sec_group, sec_group.id

    @timeout(600, "launch instance timed out!")
    def launch(self):
        machine = self.client.create_server(
            'wolkenbrot-image-creator-{}'.format(datetime.now().strftime("%Y-%m-%d_%H:%M")),  # noqa
            flavor=self.config['flavor'],
            network=self.config['networks'],
            security_groups=self.sec_group_id,
            image=self.image.id,
            key_name=self.key.id,
            userdata='manage_etc_hosts: true'
        )
        self.instance = machine

    def wait_for_status(self, status):
        """
        Wait of OS Instance to reach a certain status

        BULDING
        RUNNING
        DELELTING
        """

        while self.instance.status != status:
            print(
                "Instance: %s is in in %s state, sleeping for 5 more seconds",
            self.instance.name, self.instance.status)
            self.instance = self.client.get_server(self.instance.id)
            time.sleep(5)
        
    @timeout(600, "waiting for SSH timesout!")
    def wait_for_ssh(self):
        if self.instance.public_v4:
            ip = self.instance.public_v4
        else:
            ip = self.instance['addresses'][self.config['networks'][0]][0]['addr']

        print(f"Connecting to {ip} using key {self.key.name}")

        for i in range(0, 15):
            try:
                # TODO: fix hard coded user here
                client = SSHClient(ip, 22, 'ubuntu', None,
                                   self.key.private_key, None)
                return client
            except paramiko.ssh_exception.PasswordRequiredException as excep:
                raise excep
            except (NoValidConnectionsError, TimeoutError,
                    AuthenticationException) as e:
                print(f'Connection failed, it is likely that server is not '
                      f'ready yet. Wait 4 seconds and retry. {e}')
                time.sleep(4)

        raise ValueError('Could not connect to the machine via SSH.')


    @timeout(1200, "Copying files took too long ...")
    def copy_files(self):
        pass

    @timeout(1200, "Configure took too long ...")
    def configure(self):
        pass

    def is_image_complete(self):
        pass

    @timeout(1200, "Creating of image took too long ...")
    def create_image(self):
        pass
