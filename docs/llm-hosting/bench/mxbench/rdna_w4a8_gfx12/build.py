#!/usr/bin/env python3
"""Compile the gfx12 port into MXW4A8_BUILD_DIR (default /work/mx/build). CPU only, no GPU touch."""
import time

import mxw4a8_ext

t = time.time()
mxw4a8_ext.load_op(verbose=True)
print(f"built and loaded torch.ops.mxw4a8.gemv in {time.time() - t:.1f}s")
