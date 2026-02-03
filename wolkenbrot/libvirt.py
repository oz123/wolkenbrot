# This file is part of wolkenbrot.
# Copyright (c) 2017 - 2021 Oz Tiram <oz.tiram@gmail.com>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
# ============================================================================

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from textwrap import dedent

import libvirt
from paramiko.ssh_exception import (
    AuthenticationException,
    NoValidConnectionsError,
)

from .common import Builder
from .util import check_config, printr, printg, printy, random_name, SSHClient


class LibvirtBuilder(Builder):

    def __init__(self, client, config_params):
        self.client = client  # libvirt connection
        self.name = config_params["name"]
        self.desc = config_params.get("description", "")
        self.base_image = config_params["base_image"]["path"]
        self.config = config_params
        self.instance = None  # libvirt domain
        self.instance_name = None
        self.work_dir = None
        self.disk_path = None
        self.cloudinit_iso = None
        self.key = None
        self.key_path = None
        self.ssh_client = None
        self.sec_grp = True  # Not used in libvirt, but needed for base class
        self.sec_group_id = True

        # Libvirt-specific config
        self.memory = config_params.get("memory", 2048)  # MB
        self.vcpus = config_params.get("vcpus", 2)
        self.disk_size = config_params.get("disk_size", "20G")
        self.network = config_params.get("network", "default")

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.clean()

    def clean(self):
        if self.instance:
            self._shutdown_machine()
            self._destroy_machine()
        if self.work_dir and os.path.exists(self.work_dir):
            print(f"Cleaning up work directory {self.work_dir}...")
            shutil.rmtree(self.work_dir)

    def _shutdown_machine(self):
        if not self.instance:
            return
        try:
            state, _ = self.instance.state()
            if state == libvirt.VIR_DOMAIN_RUNNING:
                print("Shutting down imaging machine...")
                self.instance.shutdown()
                self._wait_for_shutdown()
        except libvirt.libvirtError as e:
            print(f"Error shutting down: {e}")

    def _wait_for_shutdown(self, timeout=120):
        for _ in range(timeout):
            try:
                state, _ = self.instance.state()
                if state == libvirt.VIR_DOMAIN_SHUTOFF:
                    return
            except libvirt.libvirtError:
                return
            time.sleep(1)
        # Force off if shutdown timed out
        try:
            self.instance.destroy()
        except libvirt.libvirtError:
            pass

    def _destroy_machine(self):
        if not self.instance:
            return
        try:
            print("Destroying imaging machine...")
            self.instance.undefine()
        except libvirt.libvirtError as e:
            print(f"Error destroying domain: {e}")

    def make_new_key(self):
        """Generate a temporary SSH keypair."""
        print("Creating temporary SSH keypair...")

        from io import StringIO
        import paramiko

        self.work_dir = tempfile.mkdtemp(prefix="wolkenbrot-")

        # Generate key in memory
        key = paramiko.RSAKey.generate(bits=4096)

        # Get private key as string
        private_key_io = StringIO()
        key.write_private_key(private_key_io)
        private_key = private_key_io.getvalue()

        # Get public key
        public_key = f"{key.get_name()} {key.get_base64()} wolkenbrot-builder"

        class KeyPair:
            def __init__(self, name, private, public):
                self.name = name
                self.private_key = private
                self.public_key = public

        return KeyPair(random_name("tmp_key_", 10), private_key, public_key)

    def make_new_group(self):
        """No security groups in libvirt - return dummy values."""
        return "libvirt-local", "local", True

    def _create_cloud_init_iso(self):
        """Create cloud-init ISO for VM initialization."""
        print("Creating cloud-init configuration...")

        ci_dir = os.path.join(self.work_dir, "cloud-init")
        os.makedirs(ci_dir, exist_ok=True)

        user = self.config.get("user", "ubuntu")

        user_data = dedent(f"""\
            #cloud-config
            users:
              - name: {user}
                sudo: ALL=(ALL) NOPASSWD:ALL
                shell: /bin/bash
                ssh_authorized_keys:
                  - {self.key.public_key}
            ssh_pwauth: false
            manage_etc_hosts: true
            package_update: false
            package_upgrade: false
        """)

        meta_data = dedent(f"""\
            instance-id: {self.instance_name}
            local-hostname: {self.instance_name}
        """)

        user_data_path = os.path.join(ci_dir, "user-data")
        meta_data_path = os.path.join(ci_dir, "meta-data")

        with open(user_data_path, "w") as f:
            f.write(user_data)
        with open(meta_data_path, "w") as f:
            f.write(meta_data)

        self.cloudinit_iso = os.path.join(self.work_dir, "cloud-init.iso")

        subprocess.run(
            ["cloud-localds", self.cloudinit_iso, user_data_path, meta_data_path],
            check=True,
            capture_output=True,
        )

        return self.cloudinit_iso

    def _prepare_disk(self):
        """Create a working copy of the base image."""
        print(f"Preparing disk from {self.base_image}...")

        self.disk_path = os.path.join(self.work_dir, "disk.qcow2")

        # Copy base image
        shutil.copy(self.base_image, self.disk_path)

        # Resize if needed
        subprocess.run(
            ["qemu-img", "resize", self.disk_path, self.disk_size],
            check=True,
            capture_output=True,
        )

        return self.disk_path

    def launch(self):
        """Launch the VM using libvirt."""
        self.instance_name = f"wolkenbrot-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

        self._prepare_disk()
        self._create_cloud_init_iso()

        print(f"Launching VM {self.instance_name}...")

        domain_xml = dedent(f"""\
            <domain type='kvm'>
              <name>{self.instance_name}</name>
              <memory unit='MiB'>{self.memory}</memory>
              <vcpu>{self.vcpus}</vcpu>
              <os>
                <type arch='x86_64'>hvm</type>
                <boot dev='hd'/>
              </os>
              <features>
                <acpi/>
                <apic/>
              </features>
              <cpu mode='host-passthrough'/>
              <devices>
                <disk type='file' device='disk'>
                  <driver name='qemu' type='qcow2'/>
                  <source file='{self.disk_path}'/>
                  <target dev='vda' bus='virtio'/>
                </disk>
                <disk type='file' device='cdrom'>
                  <driver name='qemu' type='raw'/>
                  <source file='{self.cloudinit_iso}'/>
                  <target dev='sda' bus='sata'/>
                  <readonly/>
                </disk>
                <interface type='network'>
                  <source network='{self.network}'/>
                  <model type='virtio'/>
                </interface>
                <serial type='pty'>
                  <target port='0'/>
                </serial>
                <console type='pty'>
                  <target type='serial' port='0'/>
                </console>
                <channel type='unix'>
                  <target type='virtio' name='org.qemu.guest_agent.0'/>
                </channel>
              </devices>
            </domain>
        """)

        self.instance = self.client.defineXML(domain_xml)
        self.instance.create()

        self._wait_for_running()

    def _wait_for_running(self, timeout=120):
        """Wait for VM to be running."""
        print("Waiting for VM to start...")
        for _ in range(timeout):
            state, _ = self.instance.state()
            if state == libvirt.VIR_DOMAIN_RUNNING:
                print("VM is running")
                return
            time.sleep(1)
        raise RuntimeError("VM failed to start")

    def _get_ip_address(self):
        """Get the VM's IP address."""
        # Try guest agent first
        try:
            ifaces = self.instance.interfaceAddresses(
                libvirt.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_AGENT
            )
            for iface, data in ifaces.items():
                if iface == "lo":
                    continue
                for addr in data.get("addrs", []):
                    if addr["type"] == libvirt.VIR_IP_ADDR_TYPE_IPV4:
                        return addr["addr"]
        except libvirt.libvirtError:
            pass

        # Fall back to lease lookup
        try:
            ifaces = self.instance.interfaceAddresses(
                libvirt.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_LEASE
            )
            for iface, data in ifaces.items():
                for addr in data.get("addrs", []):
                    if addr["type"] == libvirt.VIR_IP_ADDR_TYPE_IPV4:
                        return addr["addr"]
        except libvirt.libvirtError:
            pass

        return None

    def wait_for_ssh(self, timeout=300):
        """Wait for SSH to become available."""
        print("Waiting for SSH...")
        user = self.config.get("user", "ubuntu")

        start_time = time.time()
        ip_addr = None

        while time.time() - start_time < timeout:
            if not ip_addr:
                ip_addr = self._get_ip_address()
                if ip_addr:
                    print(f"Got IP address: {ip_addr}")
                else:
                    print("Waiting for IP address...")
                    time.sleep(5)
                    continue

            try:
                self.ssh_client = SSHClient(
                    ip_addr, 22, user, None, self.key.private_key, None
                )
                printg("SSH connection established")
                return
            except (NoValidConnectionsError, TimeoutError,
                    AuthenticationException, OSError) as e:
                print(f"SSH not ready yet: {e}")
                time.sleep(5)

        raise RuntimeError("Timed out waiting for SSH")

    def create_image(self):
        """Create the final image."""
        output_path = self.config.get("output_path", f"./{self.name}.qcow2")

        print("Shutting down VM for imaging...")
        self._shutdown_machine()

        print("Cleaning up image with virt-sysprep...")
        subprocess.run(
            [
                "virt-sysprep",
                "-a", self.disk_path,
                "--operations", "defaults,-ssh-userdir",
            ],
            check=True,
        )

        print(f"Copying final image to {output_path}...")
        # Compress/convert the final image
        subprocess.run(
            [
                "qemu-img", "convert",
                "-O", "qcow2",
                "-c",  # compress
                self.disk_path,
                output_path,
            ],
            check=True,
        )

        printg(f"Image created: {output_path}")
        return output_path


def get_client(uri="qemu:///system"):
    """Get a libvirt connection."""
    conn = libvirt.open(uri)
    if conn is None:
        raise RuntimeError(f"Failed to connect to {uri}")
    return conn


def list_images(image_dir="/var/lib/libvirt/images"):
    """List available images."""
    path = Path(image_dir)
    for img in path.glob("*.qcow2"):
        size = img.stat().st_size / (1024 * 1024 * 1024)
        print(f"{img.name}\t{size:.2f} GB")


def delete_image(image_path):
    """Delete an image."""
    printr(f"Deleting {image_path}...")
    os.remove(image_path)


def info_image(image_path):
    """Show info about an image."""
    if not os.path.exists(image_path):
        printr(f"Image not found: {image_path}")
        sys.exit(1)

    result = subprocess.run(
        ["qemu-img", "info", image_path],
        capture_output=True,
        text=True,
    )
    print(result.stdout)


def bake(image_config):
    """Bake an image from config."""
    with open(image_config, "r") as fd:
        config_dict = json.load(fd)

    check_config(config_dict)

    output_path = config_dict.get("output_path", f"./{config_dict['name']}.qcow2")
    if os.path.exists(output_path):
        printr(f"An image at '{output_path}' already exists!")
        sys.exit(2)

    client = get_client()
    with LibvirtBuilder(client, config_dict) as builder:
        builder.launch()
        builder.wait_for_ssh()
        builder.copy_files()
        builder.configure()
        builder.create_image()


def action(options):
    if options.cmd == "list":
        list_images(options.image_dir)

    if options.cmd == "info":
        image_path = options.imageId
        # If not a full path, look in image_dir
        if not os.path.isabs(image_path):
            image_path = os.path.join(options.image_dir, image_path)
        info_image(image_path)

    if options.cmd == "delete":
        image_path = options.imageId
        # If not a full path, look in image_dir
        if not os.path.isabs(image_path):
            image_path = os.path.join(options.image_dir, image_path)
        delete_image(image_path)

    if options.cmd == "bake":
        bake(options.image)