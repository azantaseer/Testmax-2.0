TESTMAX RUNNER
==============

A runner is the program that opens a real browser (Google Chrome) and does what TestMax asks:
scan your application, and run your test scripts. It connects OUT to TestMax; nothing connects in.

You need, on this computer (one time):
  - Google Chrome
  - Python 3.11 or newer   (https://www.python.org/downloads/ - tick "Add python.exe to PATH")
  - Node.js 20 or newer    (https://nodejs.org/)
  - internet access for the first start (it downloads the tooling)

To start:
  Windows : double-click  start-runner.bat
  macOS   : double-click  start-runner.command   (first time: right-click > Open)
  Linux   : run           ./start-runner.sh

The first start installs what the runner needs (a few minutes). Then a setup page opens in your
browser (it is only visible on this computer):
  1. It checks this computer.
  2. "Get pairing code" shows a code. In TestMax open  Runners > Connect a Runner  and enter the code.
  3. The runner starts by itself. Keep the console window open while it works.

Next time, just start it again: it reconnects by itself.

Where does the runner reach TestMax?  The address is already filled in on the setup page
(runner-preset.json). If your TestMax computer gets a new address, type the new one there.

The runner opens your application from THIS computer, so the application must be reachable from here.
It never holds an AI key. To disconnect: use "Disconnect this computer" on the setup page, then
revoke the runner on the TestMax Runners page.

Tested by us: Windows with Python 3.14. The macOS and Linux start files are written the same way
but have not been tried yet.
