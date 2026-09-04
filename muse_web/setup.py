from setuptools import find_packages, setup


package_name = 'muse_web'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    package_data={package_name: ['static/*', 'cloud_static/*']},
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=[
        'setuptools',
        'fastapi>=0.115,<1',
        'openpyxl>=3.1,<4',
        'uvicorn>=0.30,<1',
    ],
    zip_safe=False,
    maintainer='fernanda',
    maintainer_email='mafda4036@gmail.com',
    description='Local-first web interface for Muse research sessions.',
    license='MIT',
    extras_require={'test': ['pytest']},
    entry_points={
        'console_scripts': [
            'muse_web=muse_web.app:main',
            'muse_cloud=muse_web.cloud_app:main',
            'muse_edge_agent=muse_web.edge_agent:main',
        ],
    },
)
