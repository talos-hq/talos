#!/bin/sh
# Sign every Mach-O in a staged macOS bundle with our Developer ID Application
# certificate (hardened runtime + secure timestamp), then notarize it.
#
#   sh slim/sign_macos.sh <staged bundle dir>
#
# Env (CI secrets):
#   APPLE_DEVELOPER_ID_APP_P12           base64 .p12 (Developer ID Application)
#   APPLE_DEVELOPER_ID_APP_P12_PASSWORD
#   APPLE_TEAM_ID
#   APPLE_NOTARY_USER / APPLE_NOTARY_PASSWORD   Apple ID + app-specific password;
#                                               notarization is skipped without them.
# Gatekeeper accepts notarized loose binaries via an online ticket lookup; tickets
# can only be stapled to apps/pkgs/dmgs, so nothing is stapled here.
set -eu
bundle="$1"
[ -d "$bundle" ] || { echo "talos: no such bundle dir: $bundle" >&2; exit 1; }
: "${APPLE_DEVELOPER_ID_APP_P12:?}" "${APPLE_DEVELOPER_ID_APP_P12_PASSWORD:?}" "${APPLE_TEAM_ID:?}"

tmp="$(mktemp -d)"
keychain="$tmp/talos-signing.keychain-db"
kcpass="$(openssl rand -hex 16)"
cleanup() { security delete-keychain "$keychain" 2>/dev/null || true; rm -rf "$tmp"; }
trap cleanup EXIT

printf '%s' "$APPLE_DEVELOPER_ID_APP_P12" | base64 --decode > "$tmp/app.p12"
security create-keychain -p "$kcpass" "$keychain"
security set-keychain-settings -lut 3600 "$keychain"
security unlock-keychain -p "$kcpass" "$keychain"
security import "$tmp/app.p12" -k "$keychain" -P "$APPLE_DEVELOPER_ID_APP_P12_PASSWORD" -T /usr/bin/codesign
security set-key-partition-list -S apple-tool:,apple: -s -k "$kcpass" "$keychain" >/dev/null
security list-keychains -d user -s "$keychain" $(security list-keychains -d user | tr -d '"')
identity="Developer ID Application: Zachary Vorhies ($APPLE_TEAM_ID)"

# CPython and cffi/ctypes users need writable-executable memory; every library
# is signed by us, so library validation can stay on.
cat > "$tmp/entitlements.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>com.apple.security.cs.allow-unsigned-executable-memory</key><true/>
  <key>com.apple.security.cs.allow-jit</key><true/>
</dict></plist>
EOF

# Libraries first, executables last: a signature covers what it loads at sign time.
# Notarization rejects any unsigned Mach-O, whatever its name or mode, so classify
# every file by content: executables get the entitlements, everything else doesn't.
find "$bundle" -type f -exec file -h {} + | grep -F 'Mach-O' | grep -vF '(for architecture' > "$tmp/macho"  # universal binaries add one line per slice
grep -F 'executable' "$tmp/macho" | cut -d: -f1 > "$tmp/exes" || true
grep -vF 'executable' "$tmp/macho" | cut -d: -f1 > "$tmp/libs" || true
n=0
while IFS= read -r f; do
  codesign --force --timestamp --options runtime --keychain "$keychain" -s "$identity" "$f"; n=$((n + 1))
done < "$tmp/libs"
while IFS= read -r f; do
  codesign --force --timestamp --options runtime --entitlements "$tmp/entitlements.plist" --keychain "$keychain" -s "$identity" "$f"; n=$((n + 1))
done < "$tmp/exes"
echo "signed $n Mach-O files"
cat "$tmp/libs" "$tmp/exes" | while IFS= read -r f; do codesign --verify --strict "$f"; done

if [ -z "${APPLE_NOTARY_USER:-}" ] || [ -z "${APPLE_NOTARY_PASSWORD:-}" ]; then
  case "${GITHUB_REF:-}" in refs/tags/v*) echo "talos: release builds must be notarized; set APPLE_NOTARY_USER/APPLE_NOTARY_PASSWORD" >&2; exit 1 ;; esac
  echo "::warning::notarization secrets not set; bundle is signed but not notarized"
  exit 0
fi
ditto -c -k --keepParent "$bundle" "$tmp/notarize.zip"
xcrun notarytool submit "$tmp/notarize.zip" --apple-id "$APPLE_NOTARY_USER" --password "$APPLE_NOTARY_PASSWORD" --team-id "$APPLE_TEAM_ID" --wait --timeout 30m > "$tmp/notary.log"
cat "$tmp/notary.log"
if ! grep -q 'status: Accepted' "$tmp/notary.log"; then
  # Apple's per-file verdicts; the submission id is the first `id:` line.
  id="$(sed -n 's/^ *id: //p' "$tmp/notary.log" | head -1)"
  xcrun notarytool log "$id" --apple-id "$APPLE_NOTARY_USER" --password "$APPLE_NOTARY_PASSWORD" --team-id "$APPLE_TEAM_ID" || true
  echo "talos: notarization was not accepted" >&2; exit 1
fi
