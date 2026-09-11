#!/bin/sh
# Build the healthkicks-edge Debian package with debhelper and pybuild.
# Usage: ./build-deb.sh   (on Debian / Raspberry Pi OS, needs dpkg-dev debhelper dh-python pybuild-plugin-pyproject python3-setuptools)
#
# The packaging sources live in debian/ (debhelper convention):
#   debian/control     package metadata + Build-Depends (dh-python, setuptools)
#   debian/rules       "dh $@ --with python3 --buildsystem=pybuild" makefile
#   debian/install     system files / config -> destination mapping (dh_install)
#   debian/postinst    user/dir setup, mosquitto certs, service restarts
#   debian/prerm       service stop/disable
set -eu

# Move to folder containing the script
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

# Clean up any previous build artifacts
dh_clean

# Build the package (without signing)
dpkg-buildpackage -us -uc -b
