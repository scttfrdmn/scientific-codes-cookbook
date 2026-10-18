import ctypes
import glob
import os
import sys

try:
    libs = sorted(glob.glob(os.path.join(sys.prefix, "lib", "libopenblas*.so*")))
    lib = ctypes.CDLL(libs[0])
    lib.openblas_get_corename.restype = ctypes.c_char_p
    print("openblas_corename\t" + lib.openblas_get_corename().decode())
except Exception as e:
    print("openblas_corename\tunknown (%s)" % e)
