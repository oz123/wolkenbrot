import argparse
import sys

import colorama

from .util import check_config, printr


def get_parser():  # pragma: no coverage
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--no-color", action='store_true',
                        help="Disable colored output")

    parser.add_argument("--openstack", action='store_true')

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


def get_client_opts():
    """
    Parse the CLI options and initiate the correct client
    """
    parser = get_parser()
    options = parser.parse_args()

    colorama.init(strip=options.no_color)
    if not options.cmd:
        parser.print_help()
        sys.exit(1)

    return options


def main():
    options = get_client_opts()

    if options.openstack:
        from wolkenbrot.os import action
        action(options)
    else:
        from wolkenbrot.aws import action
        action(options)
