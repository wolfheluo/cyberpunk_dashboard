#!/usr/bin/env python3
"""Thin wrapper — delegates to init_db for historical data download."""

from init_db import init_db, download_all_historical

if __name__ == "__main__":
    init_db()
    download_all_historical()
