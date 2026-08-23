import os
from glob import glob
from setuptools import find_packages, setup

package_name = 'muse_hrc'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (
            os.path.join('share', package_name, 'launch'),
            glob(os.path.join('launch', '*launch.[pxy][yma]*')),
        ),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='fernanda',
    maintainer_email='mafda4036@gmail.com',
    description='TODO: Package description',
    license='TODO: License declaration',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': [
            'muse_node=muse_hrc.muse_node:main',
            'database_node=muse_hrc.database_node:main',
            'discovery_node=muse_hrc.discovery_node:main'
        ],
    },
)
