"""pytest plugin: give every pytest-xdist worker the same test order.

xdist requires every worker to collect the same items in the same order, and aborts with
"Different tests were collected between gw1 and gw8" otherwise. Nilearn 0.14.1 parametrizes
over a SET of function objects (plotting/image/tests/test_img_plotting.py: PLOTTING_FUNCS_3D,
used by test_plot_with_nans). A function hashes by its memory address, so each process iterates
that set in its own order. PYTHONHASHSEED cannot fix that, since it seeds string hashing only.
Measured: 8 workers happened to agree, 16 did not.

Sorting by node id after collection makes the order a function of the ids alone. It changes the
order tests run in, not which tests run or what any of them asserts.
"""
import pytest


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(session, config, items):
    items.sort(key=lambda item: item.nodeid)
