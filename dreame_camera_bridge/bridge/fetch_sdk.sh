#!/bin/sh
# Downloads Tencent's official XP2P Linux SDK and verifies it before use.
# We never redistribute the SDK: it is fetched from Tencent's own GitHub release.
set -eu
VERSION="v2.4.50"
URL="https://github.com/tencentyun/iot-p2p-build/releases/download/${VERSION}/xp2p_linux.zip"
# sha256 of the libraries we audited (see research/AUDIT_dreame_vacuum_unlocked.md)
EXPECTED_APP_INTERFACE="29c338196e22e25864e9d063f51b3067e8048717a75f656624b2653fca1f589b"
EXPECTED_ENET_CORE="acb53ad3e5d7f5659610bf397a5d46262ed29b5308792fb1040441d7fd843056"

DEST="${1:-$(dirname "$0")/vendor}"
mkdir -p "$DEST"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -fsSL -o "$TMP/sdk.zip" "$URL"
unzip -q "$TMP/sdk.zip" -d "$TMP/x"
SRC="$TMP/x/iot-p2p/iot/link/pc_app/p2p_sample"
LIB="$SRC/lib/linux/x86/Release"

check() {
    got="$(sha256sum "$1" | cut -c1-64)"
    [ "$got" = "$2" ] || { echo "SHA256 MISMATCH for $1: $got" >&2; exit 1; }
}
check "$LIB/libapp_interface.a" "$EXPECTED_APP_INTERFACE"
check "$LIB/libenet-core.a" "$EXPECTED_ENET_CORE"

rm -rf "$DEST/lib" "$DEST/include"
mkdir -p "$DEST/lib" "$DEST/include"
cp "$LIB"/*.a "$DEST/lib/"
cp "$SRC/include/appWrapper.h" "$DEST/include/"
echo "Tencent XP2P SDK ${VERSION} verified and installed in $DEST"
