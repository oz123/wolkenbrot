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

import functools
import os
import random
import string
import signal
import sys
import time

from io import StringIO

import colorama
import paramiko


def printy(string):
    print(colorama.Fore.YELLOW + string + colorama.Fore.RESET)


def printr(string):
    print(colorama.Fore.RED + string + colorama.Fore.RESET)


def printg(string):
    print(colorama.Fore.GREEN + string + colorama.Fore.RESET)


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


def random_name(prefix, length):
    name = prefix + "".join(random.choice(string.ascii_letters)
                            for i in range(length))
    return name


class TimeoutError(Exception):
    pass


class BadConfigFile(Exception):
    pass


def check_config(config):
    """
    Check the configuration file before starting to bake the image
    """
    rq = {"name", "description", "region", "user", "instance_type",
          "base_image", "uploads", "commands"}

    if config['provider'] == 'openstack':
        rq.remove('region')

    diff = rq - set(config.keys())
    if diff:
        raise(BadConfigFile("Missing keys {} in config".format(diff)))


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

        channel = self.channel

        while True:  # monitoring process
            # Reading from output streams
            while channel.recv_ready():
                received = channel.recv(1000)
                sys.stdout.write(colorama.Fore.CYAN + received.decode()
                                 + colorama.Fore.RESET)
                sys.stdout.flush()

            while channel.recv_stderr_ready():
                received = channel.recv_stderr(1000)
                sys.stderr.write(colorama.Fore.RED + received.decode() +
                                 colorama.Fore.RESET)
                sys.stderr.flush()

            if channel.exit_status_ready():  # If completed
                break
            time.sleep(0.1)

        retcode = channel.recv_exit_status()

        self.channel.close()

        return {'retval': retcode}

    def copy(self, src, dest):
        if not self.sftp:
            self.sftp = self.transport.open_sftp_client()

        try:
            self.sftp.mkdir(os.path.dirname(dest))
        except OSError:
            pass

        self.sftp.put(src, dest)
