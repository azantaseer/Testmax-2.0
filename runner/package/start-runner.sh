#!/usr/bin/env bash
# TestMax Runner: first start installs what it needs, then opens the setup page.
cd "$(dirname "$0")" || exit 1

fail() { echo; echo " $1"; echo; read -r -p " Press Enter to close. " _; exit 1; }

echo
echo " TestMax Runner"
echo " --------------"
echo

command -v python3 >/dev/null 2>&1 || fail "Python 3.11 or newer was not found. Install it from https://www.python.org/downloads/ and start this file again."
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || fail "Python 3.11 or newer is needed. Install it from https://www.python.org/downloads/ and start this file again."
command -v node >/dev/null 2>&1 || fail "Node.js was not found. Install Node.js 20 or newer from https://nodejs.org/ and start this file again."
command -v npm >/dev/null 2>&1 || fail "npm was not found. Reinstall Node.js from https://nodejs.org/ and try again."

if [ ! -x ".venv/bin/python" ]; then
  echo " Preparing a private Python environment..."
  python3 -m venv .venv || fail "Could not create the Python environment."
fi

if [ ! -f ".installed" ]; then
  echo " First start: installing the runner. This needs internet and takes a few minutes."
  echo
  .venv/bin/python -m pip install --disable-pip-version-check --quiet -r requirements-runner.txt || fail "The installation did not finish. Check your internet connection and start this file again; it continues where it stopped."
  (cd runner && npm install --no-audit --no-fund --loglevel=error && cd test-runtime && npm install --no-audit --no-fund --loglevel=error) || fail "The installation did not finish. Check your internet connection and start this file again; it continues where it stopped."
  touch .installed
  echo " Installed."
  echo
fi

.venv/bin/python -m runner setup
echo
echo " The runner has stopped. You can close this window."
read -r -p " Press Enter to close. " _
