#!/bin/bash
set -e

echo "=== LSM-Trees Lab Setup ==="

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install Python packages
pip install --quiet jupyter matplotlib

echo ""
echo "=== Setup complete ==="
echo ""
echo "To start the lab:"
echo "  source .venv/bin/activate"
echo "  jupyter notebook lab_lsm.ipynb"
