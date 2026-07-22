#!/usr/bin/env python3
"""Run the H8-22 hidden original-App update UI acceptance."""

from run_h8_21_acceptance import run


if __name__ == "__main__":
    run(wdio_config="wdio.update-ui.conf.ts")
