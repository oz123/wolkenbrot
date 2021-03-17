import os
import time

from getpass import getuser
from .util import timeout, printr, printg, printy, random_name, SSHClient


class AWSBuilder:

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
        __enter__ is called after __init__, thus keys are only
        created if __init__ was complete.
        """
        self.key = self.make_new_key()
        self.sec_grp, self.group_id = self.make_new_group()

        printy("New key {} created".format(self.key.name))
        printy("new security group {} created".format(self.sec_grp.group_name))

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        In case things get fucked up, and we encouter exceptions,
        the keys and sg created are removed.
        """
        printy("Cleaning after myself...")
        self.key.delete()
        if self.instance:
            self.instance.terminate()
            # wait for the machine to terminate
            self.wait_for_status(48)

        self.sec_grp.delete()
        os.remove(self.key.name + ".pem")
        printy("Builder teardown complete")

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
        printy("Waiting 60 seconds for SSH server to start ...")
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
            #         print("Here are the errors:")
            #        print(''.join(command_result['err']))

        printy("Finished configuration of instance")

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
