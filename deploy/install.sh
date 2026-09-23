#!/bin/sh

set -eu

release_tag="${NIYAN_INSTALL_RELEASE_TAG:-v0.4.1}"
destination="${1:-niyan-deploy}"
repository="https://raw.githubusercontent.com/niyan-project/niyan"
download_root="${repository}/${release_tag}/deploy"

fail() {
    printf 'Niyān deployment bootstrap: %s\n' "$1" >&2
    exit 1
}

command -v curl >/dev/null 2>&1 || fail 'curl is required.'
command -v mktemp >/dev/null 2>&1 || fail 'mktemp is required.'

if [ -e "$destination" ] && [ ! -d "$destination" ]; then
    fail "destination exists and is not a directory: $destination"
fi

if [ -d "$destination" ] && [ -n "$(ls -A "$destination")" ]; then
    fail "destination is not empty: $destination"
fi

temporary_directory="$(mktemp -d "${TMPDIR:-/tmp}/niyan-deploy.XXXXXX")"
trap 'rm -rf "$temporary_directory"' EXIT HUP INT TERM

for deployment_file in compose.yml Caddyfile .env.example README.md; do
    curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location --retry 3 --output "${temporary_directory}/${deployment_file}" "${download_root}/${deployment_file}"
done

mkdir -p "$destination"
for deployment_file in compose.yml Caddyfile .env.example README.md; do
    cp "${temporary_directory}/${deployment_file}" "${destination}/${deployment_file}"
done
cp "${temporary_directory}/.env.example" "${destination}/.env"
chmod 600 "${destination}/.env"

cat <<EOF
Niyān ${release_tag} deployment files are ready in ${destination}.

Next:
  1. Edit ${destination}/.env.
  2. Read ${destination}/README.md.
  3. Validate with: cd ${destination} && docker compose config --quiet

The bootstrap has not started Docker or changed the host outside that directory.
EOF
