from getpass import getuser
from .util import timeout, printr, printg, printy, random_name, SSHClient


class OpenStackBuilder:

    def __init__(self, client, config_params):
        self.client = client
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
        pass

    def __exit__(self):
        pass

    def make_new_key(self):
        pass

    def make_new_group(self):
        pass

    @timeout(600, "launch instance timed out!")
    def launch(self):
        pass

    def wait_for_status(self, status):
        pass

    @timeout(600, "waiting for SSH timesout!")
    def wait_for_ssh(self):
        pass

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
