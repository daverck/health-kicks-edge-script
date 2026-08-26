#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
VERSION=${VERSION:-0.1.0}
ARCHITECTURE=${ARCHITECTURE:-all}
PACKAGE=healthkicks-edge-agent
STAGING="$ROOT/.deb-build/${PACKAGE}_${VERSION}_${ARCHITECTURE}"
OUTPUT="$ROOT/${PACKAGE}_${VERSION}_${ARCHITECTURE}.deb"

rm -rf "$STAGING"
mkdir -p "$STAGING/DEBIAN" "$STAGING/opt/$PACKAGE" \
    "$STAGING/etc/healthkicks-edge-agent" "$STAGING/usr/lib/systemd/system" \
    "$STAGING/usr/share/doc/$PACKAGE"

cp "$ROOT/debian/control" "$STAGING/DEBIAN/control"
cp "$ROOT/debian/conffiles" "$STAGING/DEBIAN/conffiles"
cp "$ROOT/debian/postinst" "$STAGING/DEBIAN/postinst"
cp "$ROOT/debian/prerm" "$STAGING/DEBIAN/prerm"
cp "$ROOT/debian/healthkicks-edge.service" "$STAGING/usr/lib/systemd/system/healthkicks-edge.service"
cp "$ROOT/debian/agent.env.example" "$STAGING/etc/healthkicks-edge-agent/agent.env.example"
cp "$ROOT/debian/agent.env.example" "$STAGING/etc/healthkicks-edge-agent/agent.env"
cp "$ROOT/README.md" "$STAGING/usr/share/doc/$PACKAGE/README.md"
for module in main.py config.py schemas.py serial_handler.py ai_engine.py mqtt_handler.py; do
    cp "$ROOT/$module" "$STAGING/opt/$PACKAGE/"
done

chmod 0755 "$STAGING/DEBIAN/postinst" "$STAGING/DEBIAN/prerm"
chmod 0644 "$STAGING/usr/lib/systemd/system/healthkicks-edge.service" "$STAGING/etc/healthkicks-edge-agent/agent.env" "$STAGING/etc/healthkicks-edge-agent/agent.env.example"
dpkg-deb --build --root-owner-group "$STAGING" "$OUTPUT"
printf 'Built %s\n' "$OUTPUT"
