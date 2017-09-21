import argparse
import functools
import inspect
import json
import os
import random
import signal
import string
import time

from io import StringIO
from getpass import getuser
from pprint import pprint

import boto3
import paramiko


class TimeoutError(Exception):
    pass


class BadConfigFile(Exception):
    pass


def timeout(seconds, error_message='Function call timed out'):  # pragma: no coverage  # noqa
    def decorated(func):
        def _handle_timeout(signum, frame):
            raise TimeoutError(error_message)

        def wrapper(*args, **kwargs):
            signal.signal(signal.SIGALRM, _handle_timeout)
            signal.alarm(seconds)
            try:
                result = func(*args, **kwargs)
            finally:
                signal.alarm(0)
            return result

        return functools.wraps(func)(wrapper)
    return decorated


def check_config(config):
    """
    Check the configuration file before starting to bake the image
    """
    rq = {"name", "description", "region", "user", "instance_type",
          "base_image", "uploads", "commands"}
    diff = rq - set(config.keys())
    if diff:
        raise(BadConfigFile("Missing keys {} in config".format(diff)))


def random_name(prefix, length):
    name = prefix + "".join(random.choice(string.ascii_letters)
                            for i in range(length))
    return name


class SSHClient:  # pragma: no coverage

    "A wrapper of paramiko.SSHClient"

    TIMEOUT = 3600

    def __init__(self, host, port, username, password, key=None, passphrase=None):  # noqa
        self.username = username
        self.password = password
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        if key is not None:
            key = paramiko.RSAKey.from_private_key(StringIO(key),
                                                   password=passphrase)

        self.client.connect(host, port, username=username, password=password,
                            pkey=key, timeout=self.TIMEOUT)
        self.transport = self.client.get_transport()
        self.sftp = None

    def execute(self, command):
        self.channel = self.transport.open_session()
        self.channel.setblocking(1)
        self.channel.exec_command(command)

        outdata, errdata = b'', b''
        channel = self.channel

        while True:  # monitoring process
            # Reading from output streams
            while channel.recv_ready():
                outdata += channel.recv(1000)
            while channel.recv_stderr_ready():
                errdata += channel.recv_stderr(1000)
            if channel.exit_status_ready():  # If completed
                break
            time.sleep(1)

        retcode = channel.recv_exit_status()

        self.channel.close()

        return {'out': outdata.decode(),
                'err': errdata.decode(),
                'retval': retcode}

    def copy(self, src, dest):
        if not self.sftp:
            self.sftp = self.transport.open_sftp_client()

        try:
            self.sftp.mkdir(os.path.dirname(dest))
        except OSError:
            pass

        self.sftp.put(src, dest)


class Builder:

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

    def __enter__(self):
        """
        __enter__ is called after __init__, thus key as sg are only
        created if __init__ was complete.
        """
        self.key = self.make_new_key()
        self.sec_grp, self.group_id = self.make_new_group()

        print("New key {} created".format(self.key.name))
        print("new security group {} created".format(self.sec_grp.group_name))

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        In case things get fucked up, and we encouter exceptions,
        the keys and sg created are removed.
        """
        print("Cleaning after myself...")
        self.key.delete()
        if self.instance:
            self.instance.terminate()
            # wait for the machine to terminate
            self.wait_for_status(48)

        self.sec_grp.delete()
        os.remove(self.key.name + ".pem")
        print("Builder teardown complete")

    def make_new_key(self):
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

        return sec_group, sec_group.id

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
        print("Instance {} launched".format(self.reservation[0]))
        self.instance = self.reservation[0]
        print("Waiting for instance to run ...")
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
        print("The instance is now running ...")
        # The instance is running, but we give it 60 more seconds for running
        # SSHD
        print("Waiting 60 seconds for SSH server to start ...")
        time.sleep(60)

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
                    print("Successfully uploaded {} to {}".format(src, dst))

    @timeout(1200, "Configure took too long ...")
    def configure(self):
        print("starting configuration of instance")
        if not self.ssh_client:  # pragma: no coverage
            ssh_client = SSHClient(host=self.instance.public_ip_address,
                                   port=22, username=self.config["user"],
                                   password="", key=self.key.key_material)
        else:
            ssh_client = self.ssh_client

        for command in self.config["commands"]:
            print("Executing: {}".format(command))
            command_result = ssh_client.execute(command)
            ok = command_result['retval'] == 0
            print("Command '{}' {}".format(command,
                                           "succeeded" if ok else "failed!"))
            if 'out' in command_result:
                print(''.join(command_result['out']))

            if not ok:
                if 'err' in command_result:
                    print("Here are the errors:")
                    print(''.join(command_result['err']))

        print("Finished configuration of instance")

    def is_image_complete(self):
        self.image.reload()
        if self.image.state == "available":
            return True
        else:
            return False

    @timeout(1200, "Creating of image took too long ...")
    def create_image(self):
        print("Creating Image ...")
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
        print("Successuflly created {}".format(self.image.id))
        print("Waiting for the image to become available")

        while not self.is_image_complete():  # pragma: no coverage
            time.sleep(5)

        print("You image is now ready!")


def get_parser():  # pragma: no coverage
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="cmd")
    baker = subparsers.add_parser("bake", description="Create an AMI image")
    baker.add_argument("image", type=str, help="Image JSON description")

    subparsers.add_parser("list", description="List your private AMI images")

    delete = subparsers.add_parser("delete",
                                   description="Delete your private AMI image")
    delete.add_argument("imageId", type=str, help="AMI id")

    info = subparsers.add_parser("info", description="Show all info of an AMI")
    info.add_argument("imageId", type=str, help="AMI id")
    return parser


def list_images(ec2):  # pragma: no coverage
    """
    List private images you have access too
    """
    response = ec2.describe_images(Filters=[{'Name': 'is-public',
                                             'Values': ['false']}])
    response.pop('ResponseMetadata')
    print("{:12}\t{:20}\t\tCreationDate:".format("ImageId", "Name"))

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
            print(key + ":")
            pprint(value)


def delete_image(ec2, image_id):
    """
    Delete image
    """
    image = ec2.Image(image_id)
    print("Derigestering ...")
    resp = image.deregister()
    return resp


def bake(ec2, image):  # pragma: no coverage
    with open(image, "r") as fd:
        config_dict = json.load(fd)
        check_config(config_dict)
    with Builder(ec2, config_dict) as builder:
        builder.launch()
        builder.wait_for_ssh()
        builder.copy_files()
        builder.configure()
        builder.create_image()


def main():  # pragma: no coverage
    parser = get_parser()
    options = parser.parse_args()
    if not options.cmd:
        parser.print_help()

    ec2 = boto3.resource('ec2')

    if options.cmd == 'list':
        list_images(ec2.meta.client)

    if options.cmd == 'info':
        list_details(ec2, options.imageId)

    if options.cmd == 'delete':
        delete_image(ec2, options.imageId)

    if options.cmd == 'bake':
        bake(ec2, options.image)
