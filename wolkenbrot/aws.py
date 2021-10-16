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

import os
import inspect
import json
import sys
import time

from getpass import getuser
from pprint import pprint

import boto3
import paramiko

from paramiko.ssh_exception import (NoValidConnectionsError,
                                    AuthenticationException)

from .common import Builder
from .util import (check_config, timeout, printr, printg, printy,
                   random_name, SSHClient)


CLIENT  = boto3.resource('ec2')

class AWSBuilder(Builder):

    def __init__(self, ec2, config_params):
        self.ec2 = ec2
        self.name = config_params["name"]
        self.desc = config_params["description"]
        self.region = config_params["region"]
        self.instance_type = config_params["instance_type"]
        self.ami = config_params["base_image"][self.region]
        self.config = config_params
        self.tags = config_params.get("tags")
        self.key = None
        self.instance = None
        self.ssh_client = None

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        In case things get fucked up, and we encouter exceptions,
        the keys and sg created are removed.
        """
        printy("Cleaning after myself...")
        self.key.delete()
        if self.instance:
            self.instance.terminate()
        else:
            self._locate_renegade_instance()

        # wait for the machine to terminate
        self.wait_for_status(48)
        self.sec_grp.delete()
        os.remove(self.key.name + ".pem")
        printy("Builder teardown complete")

    def make_new_key(self):
        print("Creating keypair for imaging machine...")
        key_name = random_name("tmp_key_", 10)
        key = self.ec2.create_key_pair(KeyName=key_name)
        with open(key_name+'.pem', 'w') as kf:
            kf.write(key.key_material)

        return key

    def make_new_group(self):

        grp_name = random_name("tmp_grp_", 10)
        sec_group = self.ec2.create_security_group(
            GroupName=grp_name,
            Description="Wolkenbrot Temporary group for image builds"
        )
        # Now allow ssh access
        # response contains for example 'GroupId': 'sg-4bfa433b'
        sec_group.authorize_ingress(
            IpPermissions=[{'FromPort': 22, 'IpProtocol': 'TCP',
                            'ToPort': 22,
                            'IpRanges': [{'CidrIp': '0.0.0.0/0'}, ],
                            }])

        return sec_group.group_name, sec_group.id, sec_group

    def _locate_renegade_instance(self):
        """
        If an exception happends too early, it could be that an
        instance is created in AWS but never gets
        assigned to ``self.instnace``.
        """
        inst = [i for i in self.ec2.instances.filter(
                    Filters=[{'Name':'key-name', 'Values': [self.key.name]}])][-1]

        inst.terminate()
        self.instance = inst

    @timeout(600, "launch instance timed out!")
    def launch(self):
        self.reservation = self.ec2.create_instances(
            ImageId=self.ami,
            KeyName=self.key.name,
            InstanceType=self.instance_type,
            SecurityGroups=[self.sec_grp.group_name],
            MinCount=1,
            MaxCount=1,
            TagSpecifications=[
                {'ResourceType': 'instance',
                 'Tags': [{
                     "Key": "Name",
                     "Value": "Wolkenbrot image builder, by {}".format(getuser())  # noqa
                           }]
                 }]
        )

        # We are not launching more than one, so grab the first
        printy("Instance {} launched".format(self.reservation[0]))
        self.instance = self.reservation[0]
        printy("Waiting for instance to run ...")
        self.instance.wait_until_running()

    def wait_for_status(self, status):
        """
        Wait for the EC2 Instance to reach a certain status

        0 : pending
        16 : running
        32 : shutting-down
        48 : terminated
        64 : stopping
        80 : stopped

        """
        code = self.instance.state['Code']
        while code != status:
            time.sleep(3)
            self.instance.reload()
            code = self.instance.state['Code']

    @timeout(600, "waiting for SSH timesout!")
    def wait_for_ssh(self):
        """
        After the image is launched it spends a while initializing, SSH is only
        possible after this. SSH will be available shortly after the machine
        has reached the state 16.

        https://boto3.readthedocs.io/en/latest/reference/services\
                /ec2.html#EC2.Instance.state
        """
        self.wait_for_status(16)
        printy("The instance is now running ...")
        # The instance is running, but we give it 60 more seconds for running
        # SSHD
        ip_addr = self.instance.public_ip_address or self.instance.private_ip_address
        print(f"Connecting to {ip_addr} using key {self.key.name}")
        for i in range(0, 15):
            try:
                self.ssh_client = SSHClient(ip_addr, 22, self.config["user"], None,
                                   self.key.key_material, None)
                return
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
        if self.config.get("uploads"):
            for src, dst in self.config["uploads"].items():
                self.ssh_client.copy(src, dst)
                printy("Successfully uploaded {} to {}".format(src, dst))

    def is_image_complete(self):
        self.image.reload()
        if self.image.state == "available":
            return True
        else:
            return False

    @timeout(1200, "Creating of image took too long ...")
    def create_image(self):
        printy("Creating Image ...")
        self.image = self.instance.create_image(Name=self.name)
        if self.tags:
            tags = [[{"Key": k, "Value": v} for k, v in t.items()][0] for t in
                    self.tags]
        else:
            tags = []

        tags.extend([{"Key": "Description",
                      "Value": self.desc}, {"Key": "Name",
                                            "Value": self.name}])
        self.image.create_tags(Tags=tags)
        printy("Successuflly created {}".format(self.image.id))
        printy("Waiting for the image to become available")

        while not self.is_image_complete():  # pragma: no coverage
            time.sleep(5)

        printy("You image is now ready!")


def list_images(ec2):  # pragma: no coverage
    """
    List private images you have access too
    """
    response = ec2.describe_images(Filters=[{'Name': 'is-public',
                                             'Values': ['false']}])
    response.pop('ResponseMetadata')
    printy("{:12}\t{:20}\t\tCreationDate:".format("ImageId", "Name"))

    for image in response['Images']:
        if len(image["Name"]) > 20:
            image['Name'] = image['Name'][:20] + "..."
        print("{ImageId}\t{Name:20}\t\t{CreationDate}".format(**image))


def list_details(ec2, image_id):  # pragma: no coverage
    """
    Show detailed info about an image
    """
    image = ec2.Image(image_id)

    def _filter_attrs(obj):
        if isinstance(obj, property) or isinstance(obj, (str, list)):
            return True

    for key, value in inspect.getmembers(image, predicate=_filter_attrs):
        if key.startswith("_"):
            continue
        else:
            printy(key + ":")
            pprint(value)


def delete_image(ec2, image_id):
    """
    Delete image
    """
    image = ec2.Image(image_id)
    printr("Derigestering ...")
    resp = image.deregister()
    return resp


def validate_image_name(ec2, name):
    """
    Check that an image with that name does not already exist
    """
    response = ec2.describe_images(
        Filters=[{'Name': 'is-public', 'Values': ['false']},
                 {'Name': 'name', 'Values': [name]}])

    if response['Images'] and 'State' in response['Images'][0]:
        return True


def bake(ec2, image):  # pragma: no coverage
    with open(image, "r") as fd:
        config_dict = json.load(fd)

    check_config(config_dict)

    if validate_image_name(ec2.meta.client, config_dict['name']):
        printr("An image named '{}' already exists!!!".format(
            config_dict['name']))
        sys.exit(2)

    with AWSBuilder(ec2, config_dict) as builder:
        builder.launch()
        builder.wait_for_ssh()
        builder.copy_files()
        builder.configure()
        builder.create_image()


def action(options):
    if options.cmd == 'list':
        list_images(CLIENT.meta.client)

    if options.cmd == 'info':
        list_details(CLIENT, options.imageId)

    if options.cmd == 'delete':
        delete_image(CLIENT, options.imageId)

    if options.cmd == 'bake':
        bake(CLIENT, options.image)
