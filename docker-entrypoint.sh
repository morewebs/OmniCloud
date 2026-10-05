#!/bin/sh
# OmniCloud container entrypoint (self-updating container mode).
# The repo is bind-mounted at /repo from the host git checkout. On first
# boot the mount lacks the built SPA (server/static is gitignored), so we
# copy the image's prebuilt assets in. Later boots: if static/ exists it is
# already there - either ours or one built by an in-container panel update.
set -e
# The updater runs git as root against a bind-mounted host-user-owned
# checkout; without this git refuses ("detected dubious ownership") and the
# self-update feature never works.
git config --global --add safe.directory /repo
if [ ! -d /repo/server/static ] && [ -d /image-static ]; then
    mkdir -p /repo/server/static
    cp -r /image-static/. /repo/server/static/
fi
exec "$@"
