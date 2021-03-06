# -*- coding: utf-8 -*-
# app2.py
# Copyright (C) 2016-2017 LEAP Encryption Acess Project
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""
This is an alternative entrypoint for Bitmask, based on pywebview, to be used
in osx and windows.

For the moment, it requires also qt5 for the systray, but we should move to a
native solution on each platform.
"""

import getpass
import os
import platform
import signal
import sys
import time
import webbrowser

from functools import partial
from multiprocessing import Process

import webview
import psutil

from PyQt5.QtWidgets import QApplication

from leap.bitmask.core.launcher import run_bitmaskd, pid
from leap.common.config import get_path_prefix

from leap.bitmask.gui.systray import WithTrayIcon
from leap.bitmask.gui.housekeeping import cleanup, terminate, reset_authtoken
from leap.bitmask.gui.housekeeping import get_authenticated_url
from leap.bitmask.gui.housekeeping import NoAuthTokenError
from leap.bitmask.gui.housekeeping import check_stale_pidfile


DEBUG = os.environ.get("DEBUG", False)

BITMASK_URI = 'http://localhost:7070/'
PIXELATED_URI = 'http://localhost:9090/'

PROCNAME = 'bitmask-app'

qApp = None
bitmaskd = None


class Systray(WithTrayIcon):

    browser = None

    def closeFromSystray(self):
        self.user_closed = True
        if self.browser:
            self.browser.shutdown()
            self.close()
        self.close()

    def closeEvent(self, event):
        if self.user_closed:
            print "bye!"
            sys.exit()
        else:
            event.ignore()


def launch_systray():
    global qApp
    qApp = QApplication([])
    qApp.setQuitOnLastWindowClosed(True)
    systray = Systray()
    systray.setupSysTray()
    return systray


class BrowserWindow(object):
    """
    A browser window using pywebview.

    This BrowserWindow assumes that the backend is already running, since it is
    going to look for the authtoken in the configuration folder.
    """

    def __init__(self, *args, **kw):
        url = kw.pop('url', None)
        if not url:
            url = get_authenticated_url()
        self.url = url
        self.closing = False

    def create_window(self):
        # blocks. So this should be the last action you do during launch.
        webview.create_window('Bitmask', self.url)

    def loadPage(self, web_page):
        self.load(url)

    def shutdown(self, *args):
        global bitmaskd
        if self.closing:
            return
        self.closing = True
        bitmaskd.join()
        terminate(pid)
        cleanup()
        print('[bitmask] shutting down gui')


def launch_backend():
    global bitmaskd

    check_stale_pidfile()
    bitmaskd = Process(target=run_bitmaskd)
    bitmaskd.start()


def launch_gui():

    launch_backend()

    # there are some tricky movements here to synchronize
    # the different closing events:

    # 1. bitmask off button: proper way, does shutdown via js.
    # 2. systray quit: calls browser.shutdown() via reference.
    # 3. browser window close: has to call browser.shutdown() explicitely.

    try:
        systray = launch_systray()
        browser = BrowserWindow(None)
        systray.browser = browser
        browser.create_window()

        # here control is passed
        # to the pywebview event loop...

        # case 2.
        if not browser.closing:
            browser.shutdown()
        systray.browser = None

        # close systray if closed from cases 1. or 2.
        systray.closeFromSystray()
        sys.exit(qApp.exec_())

    except NoAuthTokenError as e:
        print('ERROR: ' + e.message)
        sys.exit(1)


def start_app():
    from leap.bitmask.util import STANDALONE
    mypid = os.getpid()

    # Kill a previously-running process
    for proc in psutil.process_iter():
        if proc.name() == PROCNAME and proc.pid != mypid:
            proc.kill()

    # Allow the frozen binary in the bundle double as the cli entrypoint
    # Why have only a user interface when you can have two?

    if STANDALONE and len(sys.argv) > 1:
        if sys.argv[1] == 'bitmask_helpers':
            from leap.bitmask.vpn.helpers import main
            return main()

        from leap.bitmask.cli import bitmask_cli
        return bitmask_cli.main()

    reset_authtoken()
    launch_gui()


if __name__ == "__main__":
    start_app()
