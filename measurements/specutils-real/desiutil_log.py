# Stand-in for desiutil.log, which is all the two pinned DESI sources import from desiutil.
import logging


def get_logger(*a, **k):
    return logging.getLogger("desi")
