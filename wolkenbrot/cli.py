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

import argparse
import json
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
    config_dict = {}

    if hasattr(options, 'image'):
        with open(options.image, "r") as fd:
            config_dict = json.load(fd)
    try:
        if options.openstack or config_dict.get("provider") == 'openstack':
            from wolkenbrot.os import action
            action(options)
        else:
            from wolkenbrot.aws import action
            action(options)
    except KeyboardInterrupt:
        pass
