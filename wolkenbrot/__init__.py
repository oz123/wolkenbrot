import argparse
import inspect
import json
import sys

from pprint import pprint

import boto3
import colorama

from .util import check_config, printr, printy
from .aws import AWSBuilder as Builder
from .cli import main


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

    with Builder(ec2, config_dict) as builder:
        builder.launch()
        builder.wait_for_ssh()
        builder.copy_files()
        builder.configure()
        builder.create_image()
