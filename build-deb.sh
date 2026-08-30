#!/bin/sh
# Build the healthkicks-edge Debian package with debhelper (dh).
# Usage: ./build-deb.sh   (on Debian / Raspberry Pi OS, needs dpkg-dev + debhelper)
#
# The packaging sources live in debian/ (debhelper convention):
#   debian/control     package metadata + Build-Depends
#   debian/rules       minimal "%: dh $@" makefile
#   debian/install     file -> destination mapping (dh_install)
#   debian/conffiles   /etc files preserved on upgrade
#   debian/postinst    user/dir setup, mosquitto certs, service restarts
#   debian/prerm       service stop/disable
set -eu

# Move to folder containing the script
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

# Clean up any previous build artifacts
dh_clean

# Build the package (without signing)
dpkg-buildpackage -us -uc -b
