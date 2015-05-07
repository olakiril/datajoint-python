import logging
import os

__author__ = "Dimitri Yatsenko, Edgar Walker, and Fabian Sinz at Baylor College of Medicine"
__version__ = "0.2"
__all__ = ['__author__', '__version__',
           'Connection', 'Heading', 'Base', 'Not',
           'AutoPopulate', 'conn', 'DataJointError', 'blob']


class DataJointError(Exception):
    """
    Base class for errors specific to DataJoint internal operation.
    """
    pass


# ----------- loads local configuration from file ----------------
from .settings import Config, logger, CONFIGVAR, LOCALCONFIG
config = Config()
local_config_file = os.environ.get(CONFIGVAR, None)
if local_config_file is None:
    local_config_file = os.path.expanduser(LOCALCONFIG)
else:
    local_config_file = os.path.expanduser(local_config_file)

try:
    logger.log(logging.INFO, "Loading local settings from {0:s}".format(local_config_file))
    config.load(local_config_file)
except FileNotFoundError:
    logger.warn("Local config file {0:s} does not exist! Creating it.".format(local_config_file))
    config.save(local_config_file)


# ------------- flatten import hierarchy -------------------------
from .connection import conn, Connection
from .base import Base
from .autopopulate import AutoPopulate
from . import blob
from .relational import Not