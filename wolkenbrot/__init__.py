import argparse
import inspect
import json
import sys

from pprint import pprint

import boto3
import colorama

from .util import check_config, printr, printy
from .aws import Builder


def get_parser():  # pragma: no coverage
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--no-color", action='store_true',
                        help="Disable colored output")

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


def main():  # pragma: no coverage
    parser = get_parser()
    options = parser.parse_args()

    colorama.init(strip=options.no_color)

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
