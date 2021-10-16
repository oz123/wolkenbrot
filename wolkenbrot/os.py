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
        self.sec_grp = None

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.clean()

    def clean(self):
        if self.instance:
            self._shutdown_machine()
            self._destroy_machine()
        if self.sec_group_id:
            print(f"Deleting security groups {self.sec_grp.name}...")
            self.client.delete_security_group(self.sec_group_id)
        if self.key:
            print(f"Deleting keypair {self.key.name}...")
            self.client.delete_keypair(self.key.id)
        if self.instance.volumes:
            print(f"Deleting volume {self.instance.volumes[0].id}")
            self.client.delete_volume(self.instance.volumes[0].id)

    def _shutdown_machine(self):
        print("Shutdown imaging machine...")
        instance = self.client.get_server(self.instance.id)
        if instance and instance.status != 'SHUTOFF':
            self.client.compute.stop_server(self.instance.id)

    def _destroy_machine(self):
        if self.client.get_server(self.instance.id):
            print("Destroy imaging machine...")
            self.client.delete_server(self.instance.id, wait=True, timeout=360)

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
            network=self.config['network']["name"],
            security_groups=self.sec_group_id,
            image=self.image.id,
            key_name=self.key.id,
            boot_from_volume=True,
            volume_size=25,
            userdata='manage_etc_hosts: true'
        )
        self.wait_for_status("ACTIVE")

        if self.config["network"].get('floating-ip'):
            fip = self.client.create_floating_ip()
            self.client.compute.add_floating_ip_to_server(self.instance.id,
                                                          fip.floating_ip_address)

            self.instance = self.client.get_server(self.instance.id)

    def wait_for_status(self, status, n_seconds=3):
        """
        Wait of OS Instance to reach a certain status

        BULDING
        RUNNING = ACTIVE
        DELELTING
        """

        while self.instance.status != status:
            print(
                "Instance: %s is in in %s state, sleeping for %s more seconds" %
                (self.instance.name, self.instance.status, n_seconds))
            self.instance = self.client.get_server(self.instance.id)
            time.sleep(n_seconds)

    @timeout(600, "waiting for SSH timesout!")
    def wait_for_ssh(self):
        if self.instance.public_v4:
            ip_addr = self.instance.public_v4
        else:
            ip_addr = self.instance['addresses'][self.config['network']["name"]][0]['addr']

        print(f"Connecting to {ip_addr} using key {self.key.name}")
        for i in range(0, 15):
            try:
                self.ssh_client = SSHClient(ip_addr, 22, self.config["user"], None,
                                   self.key.private_key, None)
                return
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
        """
        This method is responsible of creating the image.
        """
        self._shutdown_machine()
        self._destroy_machine()
        image_name = self.config['name']
        print(f'Creating image {image_name}')
        # delete image for this date if already existing
        if self.client.get_image(image_name):
            self.client.delete_image(image_name, wait=True, timeout=360)

        print('Waiting for volume to be available')
        for i in range(0, 360):
            status = self.client.get_volume(self.instance.volumes[0].id).status
            print("Status of volume is {}".format(status))
            if status == 'available':
                break
            time.sleep(10)

        if status != 'available':
            raise RuntimeError('Volume was has not become available during the allowed'
                               'time!')

        print('Creating image...')
        image = self.client.create_image(
            image_name,
            wait=True,
            timeout=3600,
            disk_format='raw',
            volume=self.instance.volumes[0].id
        )

        tags = self.config.get("tags", None)
        if tags:
            all_values = {}
            [all_values.update(tag) for tag in tags]
            self.client.update_image_properties(name_or_id=image.id, **all_values)

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


def delete_image(CLIENT, image_name):
    printr(f"Deleting {image_name} ...")
    img_id = CLIENT.image.find_image(image_name)
    CLIENT.image.delete_image(img_id, ignore_missing=True)


def validate_image_name(CLIENT, name):
    """
    Check that an image with that name does not already exist
    """
    return CLIENT.image.find_image(name)


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
