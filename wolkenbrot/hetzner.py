# This file is part of wolkenbrot.

# Copyright (c) 2017 - 2024 Oz Tiram <oz.tiram@gmail.com>
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

import json
import os
import sys
import time
from io import StringIO

import paramiko
from paramiko.ssh_exception import (NoValidConnectionsError,
                                    AuthenticationException)

from hcloud import Client
from hcloud.images.domain import Image
from hcloud.server_types.domain import ServerType
from hcloud.firewalls.domain import FirewallRule, FirewallResource
from hcloud.locations.domain import Location
from hcloud.servers.domain import Server

from .common import Builder
from .util import (check_config, timeout, printr, printg,
                   printy, random_name, SSHClient)


def _get_client():
    token = os.environ.get("HCLOUD_TOKEN")
    if not token:
        printr("HCLOUD_TOKEN environment variable is not set!")
        sys.exit(1)
    return Client(token=token)


class HetznerBuilder(Builder):

    def __init__(self, client, config_params):
        self.client = client
        self.name = config_params["name"]
        self.desc = config_params["description"]
        self.instance_type = config_params["instance_type"]
        self.base_image_name = config_params["base_image"]["name"]
        self.location = config_params["region"]
        self.config = config_params
        self.tags = config_params.get("tags", [])
        self.key = None
        self._private_key = None
        self.instance = None
        self.ssh_client = None
        self.sec_grp = None
        self.sec_group_id = None

    def __exit__(self, exc_type, exc_val, exc_tb):
        printy("Cleaning after myself...")
        if self.instance:
            printy(f"Deleting server {self.instance.name}...")
            action = self.instance.delete()
            action.wait_until_finished()
        if self.sec_grp:
            printy(f"Deleting firewall {self.sec_grp.name}...")
            self.sec_grp.delete()
        if self.key:
            printy(f"Deleting SSH key {self.key.name}...")
            self.key.delete()
        printy("Builder teardown complete")

    def make_new_key(self):
        print("Creating SSH key for imaging machine...")
        key_name = random_name("tmp_key_", 10)
        rsa_key = paramiko.RSAKey.generate(4096)
        private_key_io = StringIO()
        rsa_key.write_private_key(private_key_io)
        self._private_key = private_key_io.getvalue()
        public_key_str = f"ssh-rsa {rsa_key.get_base64()} wolkenbrot"
        ssh_key = self.client.ssh_keys.create(name=key_name, public_key=public_key_str)
        return ssh_key

    def make_new_group(self):
        print("Creating firewall...")
        name = random_name("tmp_fw_", 10)
        rules = [
            FirewallRule(
                direction="in",
                protocol="tcp",
                port="22",
                source_ips=["0.0.0.0/0", "::/0"],
                description="Allow SSH",
            )
        ]
        response = self.client.firewalls.create(name=name, rules=rules)
        firewall = response.firewall
        return firewall.name, firewall.id, firewall

    @timeout(600, "launch instance timed out!")
    def launch(self):
        printy(f"Launching instance with image {self.base_image_name}...")
        response = self.client.servers.create(
            name=random_name("wolkenbrot-", 10),
            server_type=ServerType(name=self.instance_type),
            image=Image(name=self.base_image_name),
            ssh_keys=[self.key],
            location=Location(name=self.location),
        )
        self.instance = response.server

        if response.action:
            response.action.wait_until_finished()

        # Attach firewall after creation
        apply_response = self.client.firewalls.apply_to_resources(
            firewall=self.sec_grp,
            resources=[FirewallResource(type="server",
                                        server=Server(id=self.instance.id))],
        )
        for action in (apply_response or []):
            action.wait_until_finished()

        printy(f"Instance {self.instance.name} launched")
        self.wait_for_status("running")

    def wait_for_status(self, status, n_seconds=3):
        while True:
            server = self.client.servers.get_by_id(self.instance.id)
            if server.status == status:
                break
            printy(f"Instance is '{server.status}', waiting for '{status}'...")
            time.sleep(n_seconds)
        self.instance = server

    @timeout(600, "waiting for SSH timed out!")
    def wait_for_ssh(self):
        ip_addr = self.instance.public_net.ipv4.ip
        print(f"Connecting to {ip_addr} using generated key")
        for i in range(0, 15):
            try:
                self.ssh_client = SSHClient(ip_addr, 22, self.config["user"],
                                            None, self._private_key, None)
                return
            except paramiko.ssh_exception.PasswordRequiredException as excep:
                raise excep
            except (NoValidConnectionsError, TimeoutError,
                    AuthenticationException) as e:
                print(f'Connection failed, server not ready yet. Retrying... {e}')
                time.sleep(4)
        raise ValueError('Could not connect to the machine via SSH.')

    @timeout(1200, "Creating image took too long...")
    def create_image(self):
        printy("Shutting down instance for clean snapshot...")
        action = self.instance.shutdown()
        action.wait_until_finished()
        self.wait_for_status("off")

        labels = {}
        if self.tags:
            for tag in self.tags:
                labels.update(tag)

        printy(f"Creating snapshot '{self.name}'...")
        response = self.instance.create_image(
            description=self.name,
            type="snapshot",
            labels=labels,
        )
        if response.action:
            response.action.wait_until_finished()

        printy(f"Snapshot '{self.name}' created with ID {response.image.id}")


def list_images(client):
    printy("{:12}\t{:30}\t\tCreated:".format("ID", "Description"))
    for image in client.images.get_all(type="snapshot"):
        desc = (image.description or "")[:30]
        print(f"{image.id}\t{desc:30}\t\t{image.created}")


def list_details(client, image_id):
    from pprint import pprint
    image = client.images.get_by_id(int(image_id))
    for attr in ["id", "name", "description", "type", "status", "created",
                 "disk_size", "image_size", "os_flavor", "os_version", "labels"]:
        printy(f"{attr}:")
        pprint(getattr(image, attr, None))


def delete_image(client, image_id):
    printr(f"Deleting image {image_id}...")
    image = client.images.get_by_id(int(image_id))
    image.delete()
    printg(f"Image {image_id} deleted.")


def validate_image_name(client, name):
    for image in client.images.get_all(type="snapshot"):
        if image.description == name:
            return True
    return False


def bake(client, image):
    with open(image, "r") as fd:
        config_dict = json.load(fd)

    check_config(config_dict)

    if validate_image_name(client, config_dict['name']):
        printr(f"An image named '{config_dict['name']}' already exists!!!")
        sys.exit(2)

    with HetznerBuilder(client, config_dict) as builder:
        builder.launch()
        builder.wait_for_ssh()
        builder.copy_files()
        builder.configure()
        builder.create_image()


def action(options):
    client = _get_client()

    if options.cmd == 'list':
        list_images(client)

    if options.cmd == 'info':
        list_details(client, options.imageId)

    if options.cmd == 'delete':
        delete_image(client, options.imageId)

    if options.cmd == 'bake':
        bake(client, options.image)
