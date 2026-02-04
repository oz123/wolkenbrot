# build a "static" executable with pyinstaller on rockylinux9
# run:
# $ docker run -v $(pwd):/usr/src -it docker.io/oz123/pyinstall-builder:latest bash
# in-container# dnf config-manager --set-enabled crb
# in-container# dnf install -y python3.12-devel python3.12-pip libvirt-devel
# in-container# pip3.12 install -e .[openstack,aws,libvirt]
# in-container# pip3.12 install pyinstaller
# in-container# pyinstaller

from pkg_resources import resource_filename, Requirement, get_distribution
import os

block_cipher = None


def Entrypoint(dist, group, name, **kwargs):
    import pkg_resources

    # get toplevel packages of distribution from metadata
    def get_toplevel(dist):
        distribution = pkg_resources.get_distribution(dist)
        if distribution.has_metadata('top_level.txt'):
            return list(distribution.get_metadata('top_level.txt').split())
        else:
            return []

    kwargs.setdefault('pathex', [])
    # get the entry point
    ep = pkg_resources.get_entry_info(dist, group, name)
    # insert path of the egg at the verify front of the search path
    kwargs['pathex'] = [ep.dist.location] + kwargs['pathex']
    # script name must not be a valid module name to avoid name clashes on import
    script_path = os.path.join(workpath, name + '-launcher')
    print("creating script for entry point", dist, group, name)
    with open(script_path, 'w') as fh:
        print("import", ep.module_name, file=fh)
        print("%s.%s()" % (ep.module_name, '.'.join(ep.attrs)), file=fh)
        #for package in packages:
        #    print("import", package, file=fh)

    return Analysis(
        [script_path] + kwargs.get('scripts', []),
        **kwargs
    )


os_service_types = resource_filename(Requirement("os_service_types"),
                                     "os_service_types")
os_defaults = resource_filename(Requirement('openstacksdk'), 'openstack')

os_service_types_ = get_distribution('os_service_types')
keystoneauth1 = get_distribution('keystoneauth1')
sdk_dist = get_distribution('openstacksdk')
munch = get_distribution('munch')


a = Entrypoint('wolkenbrot', 'console_scripts', 'wolkenbrot',
               datas=[(os_service_types, 'os_service_types'),
                      (os_defaults, 'openstack'),
                      (keystoneauth1.egg_info,
                       'keystoneauth1-%s.dist-info' % keystoneauth1.parsed_version.base_version),
                      (os_service_types_.egg_info,
                       'os_service_types-%s.dist-info' % os_service_types_.parsed_version.base_version),
                      (sdk_dist.egg_info, 'openstacksdk-%s.dist-info' % sdk_dist.parsed_version.base_version),
                      (munch.egg_info, 'munch-%s.dist-info' % munch.parsed_version.base_version),
                      ],
               hiddenimports=['novaclient.v2', 'cinderclient.v3',
                              'keystoneauth1', 'keystoneclient',
                              'keystoneauth1.loading._plugins',
                              'keystoneauth1.loading._plugins.identity',
                              'keystoneauth1.loading._plugins.identity.generic',
                              'keystoneauth1.identity',
                              'os_service_types',
			      'osc_lib',
                              'openstacksdk',
                              'openstack',
                              'octaviaclient.api',
                              'munch'])


pyz = PYZ(a.pure, a.zipped_data,
          cipher=block_cipher)


exe = EXE(pyz,
          a.scripts,
          a.binaries,
          a.zipfiles,
          a.datas,
          [],
          name='wolkenbrot',
          debug=False,
          bootloader_ignore_signals=False,
          strip=False,
          upx=True,
          runtime_tmpdir=None,
          console=True)


coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    name='wolkenbrot-dir',
    strip=False,
    upx=True
)
