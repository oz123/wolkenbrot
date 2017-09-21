from unittest.mock import Mock, patch

import boto3
import pytest

from moto import mock_ec2

from wolkenbrot import check_config, BadConfigFile
from wolkenbrot import Builder, delete_image


valid = {
    "name": "short",
    "description": "Short example for testing.",
    "user": "ubuntu",
    "region": "us-west-2",
    "instance_type": "m3.medium",
    "base_image": {
        "doc-string": "These are the U14.04 amd64 ebs images as of 3-Aug-2014",
        "us-west-2": "ami-9986fea9"
    },
    "uploads": {
        "doc-string": ("Everthing in the source dir will be uploaded to "
                       "uploads directory"),
        "source": "/home/ubuntu/source",
        "target": "/home/ubuntu/target"
    },
    "commands": ["apt-get update",
                 "apt-get upgrade -y"]
}


class MockDict:

    def __init__(self, in_dict, values=(), clear=False):
        self.in_dict = in_dict
        keys, values = zip(*values)
        self.keys = list(keys)
        self.values = list(values)

    def __enter__(self):
        self.orig_dict = self.in_dict.copy()
        return self

    def __exit__(self, *args):

        return False

    def __getitem__(self, key):
        try:
            idx = self.keys.index(key)
            self.keys.pop(idx)
            val = self.values.pop(idx)
            return val
        except ValueError:
            raise KeyError(key)


def test_valid_config():

    assert check_config(valid) is None


def test_invalid_config():
    with pytest.raises(BadConfigFile) as bcf:
        invalid = valid.copy()
        invalid.pop('name')
        check_config(invalid)

        assert str(bcf.value) == "Missing keys {'name'} in config"


def test_builder_key():
    with mock_ec2():
        ec2 = boto3.resource('ec2', region_name='us-east-1')
        with Builder(ec2, valid) as builder:
            assert builder.key is not None


def test_sec_group():
    with mock_ec2():
        ec2 = boto3.resource('ec2', region_name='us-east-1')
        with Builder(ec2, valid) as builder:
            assert hasattr(builder, 'sec_grp')


@mock_ec2
def test_create_image():
    ec2 = boto3.resource('ec2', region_name='us-east-1')

    with Builder(ec2, valid) as builder:
        builder.launch()
        builder.create_image()

        assert builder.image.state == 'available'

    with Builder(ec2, valid) as builder:
        builder.launch()
        builder.create_image()

        assert {'Key': 'Name', 'Value': 'short'} in builder.image.tags

    valid_config_with_tags = valid.copy()
    valid_config_with_tags['tags'] = [{"owner": "oznt"},
                                      {"project": "my project"}]

    with Builder(ec2, valid_config_with_tags) as builder:
        builder.launch()
        builder.create_image()

        assert {'Key': 'owner', 'Value': 'oznt'} in builder.image.tags
        assert {'Key': 'project', 'Value': 'my project'} in builder.image.tags


@mock_ec2
def test_create_image_failed():
    ec2 = boto3.resource('ec2', region_name='us-east-1')

    with Builder(ec2, valid) as builder:
        builder.image = Mock()
        builder.image.state = 'unknown'

        img_ready = builder.is_image_complete()
        assert not img_ready


@mock_ec2
def test_delete_image():
    ec2 = boto3.resource('ec2', region_name='us-east-1')
    image_id = None

    with Builder(ec2, valid) as builder:
        builder.launch()
        builder.create_image()
        image_id = builder.image._id

    response = delete_image(ec2, image_id)

    assert response['ResponseMetadata']['HTTPStatusCode'] == 200


@mock_ec2
def test_wait_for_status_running():
    ec2 = boto3.resource('ec2', region_name='us-east-1')
    with patch('time.sleep', return_value=None):
        with Builder(ec2, valid) as builder:
            builder.launch()
            original_instance = builder.instance
            builder.instance = Mock()
            with MockDict(builder.instance.state, values=(("Code", 0), ("Code", 16))) as mock_state:  # noqa
                builder.instance.state = mock_state
                builder.wait_for_status(16)
                assert builder.instance.reload.called
                builder.instance = original_instance


@mock_ec2
def test_wait_for_ssh():
    ec2 = boto3.resource('ec2', region_name='us-east-1')
    with patch('time.sleep', return_value=None) as t:
        with Builder(ec2, valid) as builder:
            builder.launch()
            original_instance = builder.instance
            builder.instance = Mock()

            with MockDict(builder.instance.state, values=(("Code", 0), ("Code", 16))) as mock_state:  # noqa
                builder.instance.state = mock_state
                builder.wait_for_ssh()
                assert builder.instance.reload.called
                builder.instance = original_instance

            # validate that we wait for ssh to start
            assert t.call_args[0][0] == 60


@patch('time.sleep', return_value=None)
@mock_ec2
def test_configure(t):
    ec2 = boto3.resource('ec2', region_name='us-east-1')
    values = (("Code", 0), ("Code", 16), ("Code", 16), ("Code", 16))
    with Builder(ec2, valid) as builder:
        builder.launch()
        original_instance = builder.instance
        builder.instance = Mock()
        with MockDict(builder.instance.state, values=values) as mock_state:  # noqa
            builder.instance.state = mock_state
            builder.wait_for_ssh()
            builder.ssh_client = Mock()
            builder.ssh_client.execute = Mock(return_value={
                "out": ("here is the output\n",), "retval": 0})

            builder.configure()
            assert builder.ssh_client.execute.called
            builder.instance = original_instance


@patch('time.sleep', return_value=None)
@patch('paramiko.SSHClient', return_value=None)
@mock_ec2
def test_configure_failed(t, p):
    ec2 = boto3.resource('ec2', region_name='us-east-1')
    values = (("Code", 0), ("Code", 16), ("Code", 16), ("Code", 16))
    with Builder(ec2, valid) as builder:
        builder.launch()
        original_instance = builder.instance
        builder.instance = Mock()
        with MockDict(builder.instance.state, values=values) as mock_state:  # noqa
            builder.instance.state = mock_state
            builder.ssh_client = Mock()
            builder.wait_for_ssh()
            builder.ssh_client.execute = Mock(return_value={
                "err": ("here is the error\n",), "retval": 1})

            builder.configure()
            assert builder.ssh_client.execute.called
            builder.instance = original_instance


@patch('time.sleep', return_value=None)
@patch('paramiko.SSHClient')
@patch('paramiko.RSAKey')
@mock_ec2
def test_upload(t, p, r):
    ec2 = boto3.resource('ec2', region_name='us-east-1')
    values = (("Code", 0), ("Code", 16), ("Code", 16), ("Code", 16))
    with Builder(ec2, valid) as builder:
        builder.launch()
        original_instance = builder.instance
        builder.instance = Mock()
        with MockDict(builder.instance.state, values=values) as mock_state:  # noqa
            builder.instance.state = mock_state
            builder.wait_for_ssh()
            builder.ssh_client = Mock()
            builder.ssh_client.copy = Mock()
            builder.copy_files()
            builder.instance = original_instance

            assert builder.ssh_client.copy.called
