#!/bin/bash
pip3 install -r requirements2.txt
python3 -m playwright install chromium
python3 -m playwright install-deps chromium
