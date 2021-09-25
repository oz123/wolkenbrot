import inspect
import json
import sys
import time

from datetime import date, datetime
from pprint import pprint

import openstack
import paramiko

from paramiko.ssh_exception import (NoValidConnectionsError,
                                    AuthenticationException)

from .common import Builder
from .util import (check_config, timeout, printr, printg,
                   printy, random_name, SSHClient)

CLIENT = openstack.connect()


class OpenStackBuilder(Builder):

    def __init__(self, client, config_params):
        self.client = client
        self.name = config_params["name"]
        self.desc = config_params["description"]
        self.instance_type = config_params["instance_type"]
        self.image = CLIENT.image.find_image(config_params["base_image"]["name"])
        if not self.image:
            raise ValueError("Could not find base image")
        self.config = config_params
        self.tags = config_params.get("tags")
        self.key = None
        self.instance = None
        self.ssh_client = None
        self.sec_group_id = None
        self.sec_group = None

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

        return sec_group.name, sec_group.id, sec_group

    @timeout(600, "launch instance timed out!")
    def launch(self):
        self.instance = self.client.create_server(
            'wolkenbrot-image-creator-{}'.format(datetime.now().strftime("%Y-%m-%d_%H:%M")),  # noqa
            flavor=self.config['instance_type'],
            network=self.config['network'],
            security_groups=self.sec_group_id,
            image=self.image.id,
            key_name=self.key.id,
            userdata='manage_etc_hosts: true'
        )

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
            ip = self.instance['addresses'][self.config['network'][0]][0]['addr']

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

    def is_image_complete(self):
        pass

    @timeout(1200, "Creating of image took too long ...")
    def create_image(self):
        pass


def list_images(CLIENT):
    for image in CLIENT.list_images():
        print("{id}\t{name:20}\t\t{created}".format(**image))
    

def list_details(CLIENT, image_id):
    image = CLIENT.image.find_image("00fe5e3a-7c97-4071-be12-6ce7d1a5ecf5")
    def _filter_attrs(obj):
        if isinstance(obj, property) or isinstance(obj, (str, list)):
            return True

    for key, value in inspect.getmembers(image, predicate=_filter_attrs):
        if key.startswith("_"):
            continue
        else:
            printy(key + ":")
            pprint(value)


def delete_image(CLIENT, image_id):
    printr("Deleting ...")
    CLIENT.image.delete_image(image_id, ignore_missing=True)


def validate_image_name(ec2, name):
    """
    Check that an image with that name does not already exist
    """
    response = CLIENT.image.find_image("Rancheros Openstack")

    return False if response else True


def bake(CLIENT, image):  # pragma: no coverage
    with open(image, "r") as fd:
        config_dict = json.load(fd)

    check_config(config_dict)

    if validate_image_name(CLIENT, config_dict['name']):
        printr("An image named '{}' already exists!!!".format(
            config_dict['name']))
        sys.exit(2)

    with OpenStackBuilder(CLIENT, config_dict) as builder:
        builder.launch()
        builder.wait_for_ssh()
        builder.copy_files()
        builder.configure()
        builder.create_image()


def action(options):
    if options.cmd == 'list':
        list_images(CLIENT)

    if options.cmd == 'info':
        list_details(CLIENT, options.imageId)

    if options.cmd == 'delete':
        delete_image(CLIENT, options.imageId)

    if options.cmd == 'bake':
        bake(CLIENT, options.image)
