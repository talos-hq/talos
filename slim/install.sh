#!/bin/sh
# talos installer for macOS and Linux (glibc or musl).
#
#   sh install.sh [--version vX.Y.Z | --archive FILE] [--dir ~/.talos] [--upgrade] [-- <talos-setup args>]
#
# --upgrade (what `hermes update` runs) keeps the existing configuration and only
# restarts the gateway; without it, setup asks for (or takes) every value.
#
# --archive installs a bundle already on disk (offline installs, CI); its
# FILE.sha256 is checked when present.
#
# Downloads one prebuilt bundle from this repo's GitHub releases ($repo below),
# verifies its sha256, unpacks it, and runs talos-setup. Nothing is
# compiled and no package manager runs.
set -eu

repo="talos-hq/talos"
version="latest"
archive=""
upgrade=false
# TALOS_DIR, then the legacy HERMES_SLIM_DIR; empty means "default" (resolved below).
dir="${TALOS_DIR:-${HERMES_SLIM_DIR:-}}"
while [ $# -gt 0 ]; do
  case "$1" in
    --version) version="$2"; shift 2 ;;
    --dir) dir="$2"; shift 2 ;;
    --archive) archive="$2"; shift 2 ;;
    --upgrade) upgrade=true; shift ;;
    --) shift; break ;;
    *) break ;;
  esac
done

# Default install dir is ~/.talos. An install from before the rename lives in
# ~/.hermes-slim: move it over (gateway stopped first) so it is upgraded in place,
# keeping a symlink at the old path for anything that still points there.
# An explicit --dir (what `hermes update` passes) is always used as given.
if [ -z "$dir" ]; then
  dir="$HOME/.talos"
  legacy_dir="$HOME/.hermes-slim"  # legacy install dir (pre-rename)
  if [ -d "$legacy_dir" ] && [ ! -L "$legacy_dir" ] && [ ! -e "$dir" ]; then
    echo "→ moving $legacy_dir to $dir"
    "$legacy_dir/current/bin/hermes" gateway stop >/dev/null 2>&1 || true
    if mv "$legacy_dir" "$dir"; then
      ln -s "$dir" "$legacy_dir" || true
    else
      echo "talos: could not move $legacy_dir; upgrading it in place" >&2
      dir="$legacy_dir"
    fi
  fi
fi

case "$(uname -s)-$(uname -m)" in
  Darwin-x86_64) target=darwin-x64 ;;
  Darwin-arm64) target=darwin-arm64 ;;
  Linux-x86_64)
    # musl (Alpine) and glibc (everything else) need different bundles: neither's
    # interpreter or compiled wheels load on the other.
    if ls /lib/ld-musl-*.so.1 >/dev/null 2>&1; then
      target=linux-x64-musl
    else
      target=linux-x64
      glibc="$(getconf GNU_LIBC_VERSION 2>/dev/null | sed 's/^glibc //')"
      case "$glibc" in
        2.[0-9]|2.1[0-9]|2.2[0-7]) echo "talos: needs glibc 2.28 or newer, this system has $glibc" >&2; exit 1 ;;
      esac
    fi ;;
  Linux-aarch64|Linux-arm64)
    # glibc only: no musl arm64 bundle yet.
    if ls /lib/ld-musl-*.so.1 >/dev/null 2>&1; then
      echo "talos: no bundle for musl (Alpine) on arm64 yet" >&2; exit 1
    fi
    target=linux-arm64
    glibc="$(getconf GNU_LIBC_VERSION 2>/dev/null | sed 's/^glibc //')"
    case "$glibc" in
      2.[0-9]|2.1[0-9]|2.2[0-7]) echo "talos: needs glibc 2.28 or newer, this system has $glibc" >&2; exit 1 ;;
    esac ;;
  *) echo "talos: unsupported platform $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac

# Bundles are .tar.zst (zstd -9). bsdtar (macOS, most BSDs) reads them itself;
# otherwise use the host's zstd, or the static one published beside the bundles
# (Linux x86_64 only: stock Debian slim and many minimal images have no zstd).
unpack() {
  if command -v zstd >/dev/null 2>&1; then
    zstd -dcq "$1" | tar -xf - -C "$2" --strip-components 1
  elif tar --version 2>/dev/null | grep -q bsdtar; then
    tar -xf "$1" -C "$2" --strip-components 1
  elif [ "$target" = linux-x64 ] || [ "$target" = linux-x64-musl ]; then
    if [ -n "$base" ]; then
      echo "→ no zstd here; fetching a static one"
      curl -fsSL -o "$tmp/zstd" "$base/zstd-linux-x64"
      curl -fsSL -o "$tmp/zstd.sha256" "$base/zstd-linux-x64.sha256"
    else
      cp "$(dirname "$1")/zstd-linux-x64" "$tmp/zstd" 2>/dev/null &&
        cp "$(dirname "$1")/zstd-linux-x64.sha256" "$tmp/zstd.sha256" 2>/dev/null ||
        { echo "talos: no zstd; install it, or put zstd-linux-x64(.sha256) beside the archive" >&2; exit 1; }
    fi
    [ "$(cut -d' ' -f1 "$tmp/zstd.sha256")" = "$(sha256 "$tmp/zstd")" ] || { echo "talos: zstd sha256 mismatch" >&2; exit 1; }
    chmod +x "$tmp/zstd"
    "$tmp/zstd" -dcq "$1" | tar -xf - -C "$2" --strip-components 1
  else
    echo "talos: unpacking needs zstd (apt install zstd / dnf install zstd / brew install zstd)" >&2
    exit 1
  fi
}

sha256() { (shasum -a 256 "$1" 2>/dev/null || sha256sum "$1") | cut -d' ' -f1; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
base=""
if [ -n "$archive" ]; then
  [ -f "$archive" ] || { echo "talos: no such archive: $archive" >&2; exit 1; }
  if [ -f "$archive.sha256" ]; then
    [ "$(cut -d' ' -f1 "$archive.sha256")" = "$(sha256 "$archive")" ] || { echo "talos: sha256 mismatch" >&2; exit 1; }
  fi
else
  if [ "$version" = latest ]; then
    base="https://github.com/$repo/releases/latest/download"
    version="$(curl -fsSLI -o /dev/null -w '%{url_effective}' "https://github.com/$repo/releases/latest" | sed 's#.*/##')"
  else
    base="https://github.com/$repo/releases/download/$version"
  fi
  release="$(curl -fsSL "https://api.github.com/repos/$repo/releases/tags/$version")"
  # Releases from before the rename carry hermes-slim-<ver>-<target> assets (legacy prefix).
  for prefix in talos hermes-slim; do
    asset="$(printf '%s\n' "$release" |
      sed -n "s/.*\"name\": *\"\($prefix-[^\"]*-$target\.tar\.zst\)\".*/\1/p" | head -1)"
    [ -z "$asset" ] || break
  done
  [ -n "$asset" ] || { echo "talos: no $target bundle in release $version" >&2; exit 1; }
  echo "→ downloading $asset"
  curl -fSL --progress-bar -o "$tmp/$asset" "$base/$asset"
  curl -fsSL -o "$tmp/$asset.sha256" "$base/$asset.sha256"
  [ "$(cut -d' ' -f1 "$tmp/$asset.sha256")" = "$(sha256 "$tmp/$asset")" ] || { echo "talos: sha256 mismatch" >&2; exit 1; }
  archive="$tmp/$asset"
fi

echo "→ installing to $dir"
mkdir -p "$dir"
rm -rf "$dir/talos.new"
mkdir "$dir/talos.new"
unpack "$archive" "$dir/talos.new" || {
  rm -rf "$dir/talos.new"
  echo "talos: could not unpack $archive (incomplete or corrupt download?); the existing install is untouched" >&2
  exit 1
}
rm -rf "$dir/talos.old"
if [ -d "$dir/current" ]; then
  # Otherwise the old gateway keeps answering /health from the replaced tree, and
  # setup takes it for the new one.
  "$dir/current/bin/hermes" gateway stop >/dev/null 2>&1 || true
  mv "$dir/current" "$dir/talos.old"
fi
mv "$dir/talos.new" "$dir/current"
# Unsigned bundle: clear the download quarantine so Gatekeeper does not block the interpreter.
[ "$(uname -s)" = Darwin ] && xattr -dr com.apple.quarantine "$dir/current" 2>/dev/null || true

mkdir -p "$HOME/.local/bin"
ln -sf "$dir/current/bin/hermes" "$HOME/.local/bin/hermes"
ln -sf "$dir/current/bin/talos-setup" "$HOME/.local/bin/talos-setup"
# Legacy hermes-slim-setup link: point it at the new launcher if this bundle has no old one.
if [ -L "$HOME/.local/bin/hermes-slim-setup" ] && [ ! -e "$dir/current/bin/hermes-slim-setup" ]; then
  ln -sf "$dir/current/bin/talos-setup" "$HOME/.local/bin/hermes-slim-setup"
fi
echo "✓ installed; 'hermes' is linked in ~/.local/bin"
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *)
    # Fresh users on Debian, Alpine and macOS do not have ~/.local/bin on PATH, and
    # `hermes` would be "command not found" after install. Login shells read ~/.profile;
    # zsh (macOS default) reads ~/.zprofile.
    line='export PATH="$HOME/.local/bin:$PATH"  # talos'
    for rc in "$HOME/.profile" "$HOME/.zprofile"; do
      { [ "$rc" = "$HOME/.profile" ] || [ -e "$rc" ] || [ "$(uname -s)" = Darwin ]; } || continue
      grep -qs -e "# talos" -e "# hermes-slim" "$rc" || printf '\n%s\n' "$line" >> "$rc"
    done
    echo "  added ~/.local/bin to PATH for new shells; in this one run: export PATH=\"\$HOME/.local/bin:\$PATH\""
    ;;
esac

# `curl ... | sh`: stdin is this script, so setup would see no terminal and refuse to
# prompt. Give it the terminal the user is sitting at, when there is one.
if [ ! -t 0 ] && (: </dev/tty) 2>/dev/null; then
  exec </dev/tty
fi
if [ "$upgrade" = true ]; then
  exec "$dir/current/bin/talos-setup" --gateway-only --service "$@"
fi
exec "$dir/current/bin/talos-setup" --service "$@"
