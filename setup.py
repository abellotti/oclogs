from setuptools import setup

setup(
    name='oclogs',
    version='0.1',
    py_modules=['oclogs'],
    install_requires=[
        'Click',
        'arrow',
        'requests',
        'crayons',
    ],
    entry_points='''
        [console_scripts]
        ocl=oclogs:main
    ''',
)
