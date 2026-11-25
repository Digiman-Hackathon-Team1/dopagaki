#!/bin/sh
set -e
cd "$(dirname "$0")/.."

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

git clone --quiet --depth 1 --filter=blob:none --sparse https://github.com/darkhos/dopagaki.git "$tmp/src"
git -C "$tmp/src" sparse-checkout set public/official-proofs public/official-proofs-ja
cp -R "$tmp/src/public/official-proofs" "$tmp/src/public/official-proofs-ja" public/
