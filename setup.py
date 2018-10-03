from setuptools import setup

setup(
    name='oclogs',
    version='0.2',
    py_modules=['oclogs'],
    install_requires=[
        'Click',
        'arrow',
        'requests',
        'crayons',
        'slackclient'
    ],
    entry_points='''
        [console_scripts]
        ocl=oclogs:main
    ''',
)
