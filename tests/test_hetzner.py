from unittest.mock import MagicMock, patch

import pytest

from wolkenbrot.util import check_config, BadConfigFile
from wolkenbrot.hetzner import (HetznerBuilder, list_images, delete_image,
                                 validate_image_name)


valid = {
    "name": "my-custom-image",
    "description": "My custom Hetzner Cloud image",
    "provider": "hetzner",
    "user": "root",
    "region": "nbg1",
    "instance_type": "cx23",
    "base_image": {
        "name": "ubuntu-24.04"
    },
    "uploads": {},
    "commands": [
        "apt-get update -y",
        "apt-get install -y nginx",
    ],
}


@pytest.fixture
def mock_client():
    client = MagicMock()

    ssh_key = MagicMock()
    ssh_key.name = "tmp_key_abc"
    ssh_key.id = 1
    client.ssh_keys.create.return_value = ssh_key

    firewall = MagicMock()
    firewall.name = "tmp_fw_abc"
    firewall.id = 42
    fw_response = MagicMock()
    fw_response.firewall = firewall
    client.firewalls.create.return_value = fw_response
    client.firewalls.apply_to_resources.return_value = []

    server = MagicMock()
    server.id = 100
    server.name = "wolkenbrot-test"
    server.status = "running"
    server.public_net.ipv4.ip = "1.2.3.4"
    server_response = MagicMock()
    server_response.server = server
    server_response.action = MagicMock()
    client.servers.create.return_value = server_response
    client.servers.get_by_id.return_value = server

    return client


# ── config validation ──────────────────────────────────────────────────────────

def test_valid_config():
    assert check_config(valid) is None


def test_invalid_config_missing_name():
    invalid = valid.copy()
    invalid.pop("name")
    with pytest.raises(BadConfigFile):
        check_config(invalid)


def test_invalid_config_missing_instance_type():
    invalid = valid.copy()
    invalid.pop("instance_type")
    with pytest.raises(BadConfigFile):
        check_config(invalid)


# ── keypair ────────────────────────────────────────────────────────────────────

def test_make_new_key(mock_client):
    with HetznerBuilder(mock_client, valid) as builder:
        assert builder.key is not None
        assert builder._private_key is not None
        mock_client.ssh_keys.create.assert_called_once()
        _, kwargs = mock_client.ssh_keys.create.call_args
        assert kwargs["public_key"].startswith("ssh-rsa")


# ── firewall ───────────────────────────────────────────────────────────────────

def test_make_new_group(mock_client):
    with HetznerBuilder(mock_client, valid) as builder:
        assert builder.sec_grp is not None
        assert builder.sec_group_id == 42
        mock_client.firewalls.create.assert_called_once()
        _, kwargs = mock_client.firewalls.create.call_args
        rules = kwargs["rules"]
        assert len(rules) == 1
        assert rules[0].port == "22"
        assert rules[0].direction == "in"


# ── launch ─────────────────────────────────────────────────────────────────────

def test_launch(mock_client):
    with HetznerBuilder(mock_client, valid) as builder:
        builder.launch()
        mock_client.servers.create.assert_called_once()
        _, kwargs = mock_client.servers.create.call_args
        assert kwargs["image"].name == "ubuntu-24.04"
        assert kwargs["server_type"].name == "cx23"
        assert kwargs["location"].name == "nbg1"
        mock_client.firewalls.apply_to_resources.assert_called_once()
        assert builder.instance is not None


# ── wait_for_status ────────────────────────────────────────────────────────────

@patch("time.sleep", return_value=None)
def test_wait_for_status(mock_sleep, mock_client):
    off_server = MagicMock()
    off_server.status = "off"

    running_server = MagicMock()
    running_server.status = "running"
    running_server.id = 100

    # first call returns "running" (already at target) so loop exits immediately
    mock_client.servers.get_by_id.return_value = running_server

    with HetznerBuilder(mock_client, valid) as builder:
        builder.instance = running_server
        builder.wait_for_status("running")
        assert builder.instance.status == "running"


@patch("time.sleep", return_value=None)
def test_wait_for_status_polls_until_ready(mock_sleep, mock_client):
    initializing = MagicMock()
    initializing.status = "initializing"
    initializing.id = 100

    running = MagicMock()
    running.status = "running"
    running.id = 100

    mock_client.servers.get_by_id.side_effect = [initializing, running]

    with HetznerBuilder(mock_client, valid) as builder:
        builder.instance = initializing
        builder.wait_for_status("running")
        assert mock_sleep.called
        assert builder.instance.status == "running"


# ── wait_for_ssh ───────────────────────────────────────────────────────────────

@patch("wolkenbrot.hetzner.SSHClient")
def test_wait_for_ssh(mock_ssh, mock_client):
    with HetznerBuilder(mock_client, valid) as builder:
        builder.instance = mock_client.servers.get_by_id.return_value
        builder._private_key = "FAKE_PRIVATE_KEY"
        builder.wait_for_ssh()
        mock_ssh.assert_called_once_with(
            "1.2.3.4", 22, "root", None, "FAKE_PRIVATE_KEY", None
        )
        assert builder.ssh_client is not None


# ── create_image ───────────────────────────────────────────────────────────────

@patch("time.sleep", return_value=None)
def test_create_image(mock_sleep, mock_client):
    off_server = MagicMock()
    off_server.status = "off"
    off_server.id = 100
    mock_client.servers.get_by_id.return_value = off_server

    image_mock = MagicMock()
    image_mock.id = 999
    img_response = MagicMock()
    img_response.image = image_mock

    with HetznerBuilder(mock_client, valid) as builder:
        builder.instance = off_server
        builder.instance.create_image.return_value = img_response
        builder.create_image()

        builder.instance.shutdown.assert_called_once()
        builder.instance.create_image.assert_called_once_with(
            description="my-custom-image",
            type="snapshot",
            labels={},
        )


@patch("time.sleep", return_value=None)
def test_create_image_with_tags(mock_sleep, mock_client):
    config_with_tags = valid.copy()
    config_with_tags["tags"] = [{"env": "production"}, {"team": "ops"}]

    off_server = MagicMock()
    off_server.status = "off"
    off_server.id = 100
    mock_client.servers.get_by_id.return_value = off_server

    img_response = MagicMock()
    img_response.image.id = 1001

    with HetznerBuilder(mock_client, config_with_tags) as builder:
        builder.instance = off_server
        builder.instance.create_image.return_value = img_response
        builder.create_image()

        _, kwargs = builder.instance.create_image.call_args
        assert kwargs["labels"]["env"] == "production"
        assert kwargs["labels"]["team"] == "ops"


# ── configure / upload ─────────────────────────────────────────────────────────

def test_configure(mock_client):
    with HetznerBuilder(mock_client, valid) as builder:
        builder.ssh_client = MagicMock()
        builder.ssh_client.execute.return_value = {"retval": 0}
        builder.configure()
        assert builder.ssh_client.execute.call_count == len(valid["commands"])


def test_configure_failing_command(mock_client):
    with HetznerBuilder(mock_client, valid) as builder:
        builder.ssh_client = MagicMock()
        builder.ssh_client.execute.return_value = {"retval": 1}
        builder.configure()
        assert builder.ssh_client.execute.called


def test_copy_files(mock_client):
    config_with_uploads = valid.copy()
    config_with_uploads["uploads"] = {"local/file.txt": "/remote/file.txt"}

    with HetznerBuilder(mock_client, config_with_uploads) as builder:
        builder.ssh_client = MagicMock()
        builder.copy_files()
        builder.ssh_client.copy.assert_called_once_with(
            "local/file.txt", "/remote/file.txt"
        )


# ── image management helpers ───────────────────────────────────────────────────

def test_list_images(mock_client, capsys):
    img1 = MagicMock()
    img1.id = 1
    img1.description = "ubuntu-base"
    img1.created = "2024-01-01T00:00:00Z"

    img2 = MagicMock()
    img2.id = 2
    img2.description = "nginx-image"
    img2.created = "2024-02-01T00:00:00Z"

    mock_client.images.get_all.return_value = [img1, img2]
    list_images(mock_client)
    mock_client.images.get_all.assert_called_once_with(type="snapshot")
    out = capsys.readouterr().out
    assert "ubuntu-base" in out
    assert "nginx-image" in out


def test_delete_image(mock_client):
    image_mock = MagicMock()
    mock_client.images.get_by_id.return_value = image_mock
    delete_image(mock_client, "999")
    mock_client.images.get_by_id.assert_called_once_with(999)
    image_mock.delete.assert_called_once()


def test_validate_image_name_found(mock_client):
    img = MagicMock()
    img.description = "my-custom-image"
    mock_client.images.get_all.return_value = [img]
    assert validate_image_name(mock_client, "my-custom-image") is True


def test_validate_image_name_not_found(mock_client):
    img = MagicMock()
    img.description = "other-image"
    mock_client.images.get_all.return_value = [img]
    assert validate_image_name(mock_client, "my-custom-image") is False


# ── cleanup ────────────────────────────────────────────────────────────────────

def test_cleanup_on_exit(mock_client):
    with HetznerBuilder(mock_client, valid) as builder:
        builder.launch()

    builder.instance.delete.assert_called()
    builder.sec_grp.delete.assert_called()
    builder.key.delete.assert_called()
