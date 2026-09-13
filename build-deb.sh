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

# Ensure bluezero vendored source exists
if [ ! -d "vendor/bluezero" ] || [ ! -f "vendor/bluezero/__init__.py" ]; then
    echo "vendor/bluezero is missing, extracting from wheel..."
    python3 -c "import urllib.request, zipfile, io; r = urllib.request.urlopen('https://files.pythonhosted.org/packages/5a/d0/34d7d4f224aec600f54f2c57d24f8199608b5ad7c3fe4f740b759a1008b9/bluezero-0.9.1-py2.py3-none-any.whl'); z = zipfile.ZipFile(io.BytesIO(r.read())); [z.extract(n, 'vendor') for n in z.namelist() if n.startswith('bluezero/')]"
fi

# Clean up any previous build artifacts
dh_clean

# Build the package (without signing)
dpkg-buildpackage -us -uc -b
