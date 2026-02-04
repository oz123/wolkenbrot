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

import io
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

def _get_subprocess_env():
    """Get clean environment for subprocess calls (fixes PyInstaller issues).

    PyInstaller modifies LD_LIBRARY_PATH which breaks external binaries.
    Remove it so system binaries use system libraries.
    """
    env = os.environ.copy()
    # Detect PyInstaller and remove LD_LIBRARY_PATH
    if hasattr(sys, '_MEIPASS') or env.get("LD_LIBRARY_PATH", "").startswith("/tmp/_MEI"):
        env.pop("LD_LIBRARY_PATH", None)
    return env


def _find_qemu_img():
    """Find qemu-img binary, handling PyInstaller's modified PATH."""
    # Check common locations first (most reliable for PyInstaller)
    for candidate in ["/usr/bin/qemu-img", "/usr/local/bin/qemu-img"]:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    # Try shutil.which (works in normal Python)
    path = shutil.which("qemu-img")
    if path:
        return path
    # Last resort: hope it's in PATH
    return "qemu-img"


QEMU_IMG = _find_qemu_img()
SUBPROCESS_ENV = _get_subprocess_env()

# Predefined instance types: name -> (vcpus, memory_mb, disk_size)
INSTANCE_TYPES = {
    "small": (1, 1024, "10G"),
    "medium": (2, 4096, "20G"),
    "large": (4, 8192, "40G"),
    "xlarge": (8, 16384, "80G"),
}


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
        instance_type = config_params.get("instance_type")
        if instance_type:
            if instance_type not in INSTANCE_TYPES:
                valid = ", ".join(INSTANCE_TYPES.keys())
                raise ValueError(f"Unknown instance_type '{instance_type}'. Valid types: {valid}")
            vcpus, memory, disk_size = INSTANCE_TYPES[instance_type]
            self.vcpus = config_params.get("vcpus", vcpus)
            self.memory = config_params.get("memory", memory)
            self.disk_size = config_params.get("disk_size", disk_size)
        else:
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

        # Use /var/tmp for better qemu/libvirt access
        self.work_dir = tempfile.mkdtemp(prefix="wolkenbrot-", dir="/var/tmp")
        # Make work_dir accessible to qemu
        os.chmod(self.work_dir, 0o755)

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
        import pycdlib

        print("Creating cloud-init configuration...")

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

        self.cloudinit_iso = os.path.join(self.work_dir, "cloud-init.iso")

        iso = pycdlib.PyCdlib()
        iso.new(vol_ident="cidata", joliet=3, rock_ridge="1.09")

        user_data_bytes = user_data.encode("utf-8")
        meta_data_bytes = meta_data.encode("utf-8")

        iso.add_fp(
            io.BytesIO(user_data_bytes),
            len(user_data_bytes),
            "/USERDATA.;1",
            rr_name="user-data",
            joliet_path="/user-data",
        )
        iso.add_fp(
            io.BytesIO(meta_data_bytes),
            len(meta_data_bytes),
            "/METADATA.;1",
            rr_name="meta-data",
            joliet_path="/meta-data",
        )

        iso.write(self.cloudinit_iso)
        iso.close()

        # Make ISO accessible to qemu
        os.chmod(self.cloudinit_iso, 0o644)

        return self.cloudinit_iso

    def _prepare_disk(self):
        """Create a working copy of the base image."""
        print(f"Preparing disk from {self.base_image}...")

        self.disk_path = os.path.join(self.work_dir, "disk.qcow2")

        # Copy base image
        shutil.copy(self.base_image, self.disk_path)

        # Make disk accessible to qemu (read/write)
        os.chmod(self.disk_path, 0o666)

        # Resize if needed
        try:
            subprocess.run(
                [QEMU_IMG, "resize", self.disk_path, self.disk_size],
                check=True,
                capture_output=True,
                env=SUBPROCESS_ENV,
            )
        except subprocess.CalledProcessError as e:
            print(f"qemu-img resize failed with exit code {e.returncode}")
            print(f"stdout: {e.stdout}")
            print(f"stderr: {e.stderr}")
            print(f"env LD_LIBRARY_PATH: {SUBPROCESS_ENV.get('LD_LIBRARY_PATH', 'not set')}")
            raise

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

    def _sysprep(self):
        """Clean up the VM image for templating (like virt-sysprep)."""
        print("Preparing image for templating...")

        cleanup_commands = [
            # Remove SSH host keys (new VMs will regenerate)
            "sudo rm -f /etc/ssh/ssh_host_*",
            # Remove machine-id (will be regenerated on boot)
            "sudo truncate -s 0 /etc/machine-id",
            "sudo rm -f /var/lib/dbus/machine-id",
            # Clear cloud-init state so it runs again
            "sudo cloud-init clean --logs 2>/dev/null || true",
            # Remove temporary files
            "sudo rm -rf /tmp/* /var/tmp/*",
            # Clear shell history
            "sudo rm -f /root/.bash_history",
            f"rm -f /home/{self.config.get('user', 'ubuntu')}/.bash_history",
            # Clear logs
            "sudo find /var/log -type f -exec truncate -s 0 {} \\;",
            # Remove persistent network rules
            "sudo rm -f /etc/udev/rules.d/70-persistent-net.rules",
            # Sync filesystem
            "sync",
        ]

        for cmd in cleanup_commands:
            self.ssh_client.execute(cmd)

    def create_image(self):
        """Create the final image."""
        output_path = self.config.get("output_path", f"./{self.name}.qcow2")

        self._sysprep()

        print("Shutting down VM for imaging...")
        self._shutdown_machine()

        print(f"Copying final image to {output_path}...")
        # Compress/convert the final image
        subprocess.run(
            [
                QEMU_IMG, "convert",
                "-O", "qcow2",
                "-c",  # compress
                self.disk_path,
                output_path,
            ],
            check=True,
            env=SUBPROCESS_ENV,
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
        [QEMU_IMG, "info", image_path],
        capture_output=True,
        text=True,
        env=SUBPROCESS_ENV,
    )
    print(result.stdout)


def bake(image_config, uri=None):
    """Bake an image from config."""
    with open(image_config, "r") as fd:
        config_dict = json.load(fd)

    check_config(config_dict)

    output_path = config_dict.get("output_path", f"./{config_dict['name']}.qcow2")
    if os.path.exists(output_path):
        printr(f"An image at '{output_path}' already exists!")
        sys.exit(2)

    # URI priority: CLI option > config "region" > default
    libvirt_uri = uri or config_dict.get("region", "qemu:///system")

    client = get_client(libvirt_uri)
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
        bake(options.image, uri=options.uri)